"""Apuração Sem Biometria — soma de eleitores liberados sem biometria nos Boletins de Urna (TSE)."""
import csv
import io
import json
import multiprocessing as mp
import os
import queue
import re
import secrets
import shutil
import tempfile
import threading
import traceback
import uuid
import zipfile
from collections import Counter, deque
from concurrent.futures import ProcessPoolExecutor
from urllib.parse import urlparse

from flask import Flask, Response, abort, jsonify, render_template, request

import db
import logs
import processor
import tse

app = Flask(__name__)
db.init()

SENHA = os.environ.get("APP_PASSWORD", "")
MAX_UPLOAD = float(os.environ.get("MAX_UPLOAD_GB", "6")) * 1e9
TMP_DIR = os.environ.get("TMP_DIR", os.path.join(tempfile.gettempdir(), "apuracao"))
os.makedirs(TMP_DIR, exist_ok=True)
LOG_WORKERS = int(os.environ.get("LOG_WORKERS", str(min(4, os.cpu_count() or 1))))
HOSTS_LOG = tuple(h.strip().lower() for h in os.environ.get("LOG_URL_HOSTS", "tse.jus.br").split(",") if h.strip())

# ---------------------------------------------------------------- autenticação opcional
@app.before_request
def exigir_senha():
    if not SENHA or request.path == "/health":
        return
    auth = request.authorization
    if not auth or not secrets.compare_digest(auth.password or "", SENHA):
        return Response("Acesso restrito", 401, {"WWW-Authenticate": 'Basic realm="Apuracao"'})


# ---------------------------------------------------------------- fila de tarefas (1 por vez)
fila: "queue.Queue[tuple[int, dict]]" = queue.Queue()


def _executar(tid, job):
    def prog(fracao, texto):
        db.atualizar_tarefa(tid, progresso=round(fracao, 4), mensagem=texto)

    db.atualizar_tarefa(tid, status="executando", progresso=0, mensagem="iniciando")
    resumo = []
    rodada = {"id": None}

    def obter_rodada():
        # a rodada só é criada quando há o primeiro resultado (tarefa com erro não gera rodada vazia)
        if rodada["id"] is None:
            rodada["id"] = db.nova_rodada(tid, job.get("descricao", ""))
        return rodada["id"]

    def salvar(linhas, origem):
        return db.salvar_resultados(linhas, origem, obter_rodada())

    if job["tipo"] in ("arquivo", "logs_url"):
        caminho = job.get("caminho")
        try:
            if job["tipo"] == "logs_url":
                caminho = os.path.join(TMP_DIR, f"{uuid.uuid4().hex}_{job['nome']}")
                tse.baixar(job["url"], caminho, lambda f, t: prog(0.3 * f, t))
            if logs.eh_arquivo_de_logs(caminho):
                resumo.append(_processar_logs(tid, caminho, job, prog, obter_rodada))
            else:
                cols, secoes = processor.ler_secoes(caminho, prog)
                pares = salvar(processor.agregar_por_municipio(cols, secoes), job["nome"])
                resumo.append(", ".join(f"{t}ºT {u}" for t, u in pares))
        finally:
            if caminho and os.path.exists(caminho):
                os.remove(caminho)
    elif job["tipo"] == "logs_tse":
        arquivos = [a for a in tse.listar_logs()
                    if a["turno"] in job["turnos"] and (not job["ufs"] or a["uf"] in job["ufs"])]
        if not arquivos:
            raise RuntimeError("Nenhum arquivo de logs correspondente ao turno e às UFs escolhidos foi encontrado no TSE.")
        for n, a in enumerate(arquivos):
            base, fatia = n / len(arquivos), 1 / len(arquivos)
            rotulo = f"[{n+1}/{len(arquivos)}] logs {a['turno']}ºT {a['uf']}"
            destino = os.path.join(TMP_DIR, f"{uuid.uuid4().hex}_{a['nome']}")
            try:
                tse.baixar(a["url"], destino, lambda f, t: prog(base + fatia * 0.4 * f, f"{rotulo}: {t}"))
                sub = {"tipo": "logs_tse", "nome": a["nome"], "turno_forcado": a["turno"], "uf_forcada": a["uf"]}
                resumo.append(_processar_logs(tid, destino, sub,
                                              lambda f, t: prog(base + fatia * (0.4 + 0.6 * f), f"{rotulo}: {t}"),
                                              obter_rodada))
            finally:
                if os.path.exists(destino):
                    os.remove(destino)
    else:  # tse
        arquivos = [a for a in tse.listar_arquivos()
                    if a["turno"] in job["turnos"] and (not job["ufs"] or a["uf"] in job["ufs"])]
        if not arquivos:
            raise RuntimeError("Nenhum arquivo correspondente encontrado no Portal de Dados Abertos.")
        for n, a in enumerate(arquivos):
            base = n / len(arquivos)
            fatia = 1 / len(arquivos)
            rotulo = f"[{n+1}/{len(arquivos)}] {a['turno']}ºT {a['uf']}"
            destino = os.path.join(TMP_DIR, f"{uuid.uuid4().hex}_{a['nome']}")
            try:
                tse.baixar(a["url"], destino, lambda f, t: prog(base + fatia * 0.5 * f, f"{rotulo}: {t}"))
                cols, secoes = processor.ler_secoes(destino, lambda f, t: prog(base + fatia * (0.5 + 0.5 * f), f"{rotulo}: {t}"))
                salvar(processor.agregar_por_municipio(cols, secoes), a["nome"])
                resumo.append(f"{a['turno']}ºT {a['uf']}")
            finally:
                if os.path.exists(destino):
                    os.remove(destino)
    db.atualizar_tarefa(tid, status="concluída", progresso=1,
                        mensagem=f"Rodada #{rodada['id']} arquivada · " + ", ".join(resumo))


def padroes_log():
    salvo = db.ler_config("padroes_log")
    return logs.validar_padroes(json.loads(salvo) if salvo else {})


def _mapear(gerador):
    """Processa os logs em paralelo (processos auxiliares) mantendo poucos itens em memória."""
    if LOG_WORKERS <= 1:
        for args, meta in gerador:
            yield meta, logs._trabalho(args)
        return
    with ProcessPoolExecutor(LOG_WORKERS, mp_context=mp.get_context("spawn")) as ex:
        pendentes = deque()
        for args, meta in gerador:
            pendentes.append((meta, ex.submit(logs._trabalho, args)))
            if len(pendentes) >= LOG_WORKERS * 4:
                m, f = pendentes.popleft()
                yield m, f.result()
        while pendentes:
            m, f = pendentes.popleft()
            yield m, f.result()


def _processar_logs(tid, caminho, job, prog, obter_rodada):
    padroes = padroes_log()
    mapa_uf = {r["cd_municipio"]: r["uf"] for r in db.consultar(
        "SELECT DISTINCT cd_municipio, uf FROM resultados UNION SELECT DISTINCT cd_municipio, uf FROM rodada_resultados")}
    nome = job["nome"]
    total = None
    if caminho.lower().endswith(".zip") or not nome.lower().endswith(".logjez"):

        if zipfile.is_zipfile(caminho):
            with zipfile.ZipFile(caminho) as z:
                total = sum(1 for n in z.namelist() if n.lower().endswith(".logjez")) or None
    base_prog = 0.3 if job["tipo"] == "logs_url" else 0.0
    estado = {"coletados": 0}

    def gerador():
        for rotulo, dados in logs.iterar_logs(caminho, TMP_DIR, nome):
            cd, zona, sec = logs.identificar_secao(rotulo)
            uf = job.get("uf_forcada") or logs.detectar_uf(rotulo) or mapa_uf.get(cd) or job.get("uf")
            if not uf:
                raise RuntimeError(f"Não foi possível identificar a UF de '{rotulo}'. "
                                   "Escolha a UF no formulário e envie de novo.")
            turno = job.get("turno_forcado") or logs.detectar_turno(rotulo) or job.get("turno") or "2"
            coletar = estado["coletados"] < 30
            estado["coletados"] += coletar
            yield (rotulo, dados, padroes, uf, coletar), (turno, uf, cd, zona, sec)

    secoes, mensagens, erros = {}, Counter(), []
    feitos = 0
    for (turno, uf, cd, zona, sec), (rotulo, r, erro) in _mapear(gerador()):
        feitos += 1
        if erro:
            erros.append(f"{rotulo}: {erro}")
            continue
        mensagens.update(r.pop("mensagens", {}))
        chave = (turno, uf, cd, zona, sec)
        s = secoes.get(chave)
        if s is None:
            secoes[chave] = s = {"turno": turno, "uf": uf, "cd_municipio": cd, "zona": zona, "secao": sec,
                                 "arquivos": 0, "primeiro": None, "ultimo": None, "origem": nome,
                                 **{k: 0 for k in ("votos", "votos_apos_aj", "votos_apos_br", "sem", "sem_apos_aj", "sem_apos_br")}}
        s["arquivos"] += 1
        for k in ("votos", "votos_apos_aj", "votos_apos_br", "sem", "sem_apos_aj", "sem_apos_br"):
            s[k] += r[k]
        for k, f in (("primeiro", min), ("ultimo", max)):
            if r[k] is not None:
                s[k] = r[k] if s[k] is None else f(s[k], r[k])
        if feitos % 200 == 0:
            frac = base_prog + (1 - base_prog) * (feitos / total if total else 0)
            prog(min(frac, 0.99), f"{feitos:,} logs lidos".replace(",", ".") + (f" de {total:,}".replace(",", ".") if total else ""))

    if not secoes:
        detalhe = f" Erros: {'; '.join(erros[:3])}" if erros else ""
        raise RuntimeError("Nenhum log de urna (.logjez) foi lido." + detalhe)
    db.salvar_logs(obter_rodada(), list(secoes.values()))

    re_p = {k: re.compile(v) for k, v in padroes.items()}
    diag = {"logs": feitos, "erros": erros[:10],
            "mensagens": [[m, c, [k for k, rx in re_p.items() if rx.search(m)]] for m, c in mensagens.most_common(60)]}
    db.atualizar_tarefa(tid, diagnostico=json.dumps(diag, ensure_ascii=False))

    votos = sum(s["votos"] for s in secoes.values())
    sem = sum(s["sem"] for s in secoes.values())
    ufs = sorted({(s["turno"], s["uf"]) for s in secoes.values()})
    texto = f"Logs: {feitos:,} arquivos, {len(secoes):,} seções ({', '.join(f'{t}ºT {u}' for t, u in ufs)}), ".replace(",", ".")
    texto += f"{votos:,} votos, {sem:,} sem digital".replace(",", ".")
    if votos == 0:
        texto += " · ATENÇÃO: nenhum 'voto computado' reconhecido; confira os padrões de mensagem"
    elif sem == 0:
        texto += " · ATENÇÃO: nenhuma liberação sem digital reconhecida; confira os padrões de mensagem"
    if erros:
        texto += f" · {len(erros)} arquivo(s) com erro de leitura"
    return texto


def _trabalhador():
    while True:
        tid, job = fila.get()
        try:
            _executar(tid, job)
        except Exception as e:  # noqa: BLE001
            traceback.print_exc()
            db.atualizar_tarefa(tid, status="erro", mensagem=str(e)[:800])
        finally:
            fila.task_done()


threading.Thread(target=_trabalhador, daemon=True).start()


def enfileirar(descricao, job):
    job["descricao"] = descricao
    tid = db.nova_tarefa(descricao)
    fila.put((tid, job))
    return tid


# ---------------------------------------------------------------- páginas e consultas
@app.get("/")
def index():
    return render_template("index.html")


@app.get("/health")
def health():
    return {"ok": True}


def fonte():
    """Tabela consultada: resultados atuais ou uma rodada arquivada (?rodada=N)."""
    rid = request.args.get("rodada", "").strip()
    if rid:
        if not rid.isdigit():
            abort(400, "Rodada inválida.")
        return "rodada_resultados WHERE rodada_id=?", (int(rid),)
    return "resultados WHERE 1=1", ()


@app.get("/api/resumo")
def resumo():
    src, p = fonte()
    por_uf = db.consultar(f"""
        SELECT turno, uf, coluna, SUM(secoes) secoes, SUM(comparecimento) comparecimento,
               SUM(valor) valor, MAX(atualizado) atualizado
        FROM {src} GROUP BY turno, uf, coluna ORDER BY turno, uf, coluna""", p)
    por_turno = db.consultar(f"""
        SELECT turno, coluna, SUM(secoes) secoes, SUM(comparecimento) comparecimento,
               SUM(valor) valor, COUNT(DISTINCT uf) ufs
        FROM {src} GROUP BY turno, coluna ORDER BY turno, coluna""", p)
    return jsonify(por_turno=por_turno, por_uf=por_uf)


@app.get("/api/municipios")
def municipios():
    src, p = fonte()
    turno, uf = request.args.get("turno", ""), request.args.get("uf", "")
    linhas = db.consultar(f"""
        SELECT cd_municipio, nm_municipio, coluna, secoes, comparecimento, valor
        FROM {src} AND turno=? AND uf=? ORDER BY valor DESC""", (*p, turno, uf))
    return jsonify(linhas)


@app.get("/api/rodadas")
def rodadas():
    lista = db.consultar("SELECT id, tarefa_id, descricao, criado FROM rodadas ORDER BY id DESC LIMIT 200")
    totais = db.consultar("""
        SELECT rodada_id, turno, coluna, SUM(valor) valor, SUM(comparecimento) comparecimento,
               SUM(secoes) secoes, COUNT(DISTINCT uf) ufs
        FROM rodada_resultados GROUP BY rodada_id, turno, coluna ORDER BY turno, coluna""")
    tot_logs = db.consultar("""
        SELECT rodada_id, turno, COUNT(*) secoes, COUNT(DISTINCT uf) ufs, SUM(votos) votos,
               SUM(votos_apos_aj) votos_apos, SUM(sem) sem, SUM(sem_apos_aj) sem_apos
        FROM log_secoes GROUP BY rodada_id, turno ORDER BY turno""")
    por_id, por_id_log = {}, {}
    for t in totais:
        por_id.setdefault(t.pop("rodada_id"), []).append(t)
    for t in tot_logs:
        por_id_log.setdefault(t.pop("rodada_id"), []).append(t)
    for r in lista:
        r["totais"] = por_id.get(r["id"], [])
        r["logs"] = por_id_log.get(r["id"], [])
    return jsonify(lista)


@app.delete("/api/rodadas/<int:rid>")
def excluir_rodada(rid):
    db.excluir_rodada(rid)
    return {"ok": True}


@app.get("/api/tarefas")
def tarefas():
    return jsonify(db.consultar("SELECT * FROM tarefas ORDER BY id DESC LIMIT 15"))


@app.get("/api/export.csv")
def exportar():
    nivel = request.args.get("nivel", "uf")
    src, p = fonte()
    sufixo = f"_rodada{request.args['rodada']}" if request.args.get("rodada") else ""
    if nivel == "municipio":
        linhas = db.consultar("SELECT turno, uf, cd_municipio, nm_municipio, coluna, secoes, comparecimento, valor "
                              f"FROM {src} ORDER BY turno, uf, nm_municipio", p)
    else:
        linhas = db.consultar("SELECT turno, uf, coluna, SUM(secoes) secoes, SUM(comparecimento) comparecimento, "
                              f"SUM(valor) valor FROM {src} GROUP BY turno, uf, coluna ORDER BY turno, uf", p)
    buf = io.StringIO()
    if linhas:
        w = csv.DictWriter(buf, fieldnames=list(linhas[0].keys()), delimiter=";")
        w.writeheader()
        w.writerows(linhas)
    return Response("﻿" + buf.getvalue(), mimetype="text/csv",
                    headers={"Content-Disposition": f"attachment; filename=sem_biometria_{nivel}{sufixo}.csv"})


@app.post("/api/limpar")
def limpar():
    db.executar("DELETE FROM resultados")
    return {"ok": True}


# ---------------------------------------------------------------- download direto do TSE
@app.get("/api/tse/arquivos")
def tse_arquivos():
    try:
        return jsonify(tse.listar_arquivos())
    except Exception as e:  # noqa: BLE001
        return jsonify(erro=str(e)), 502


@app.post("/api/tse")
def tse_baixar():
    dados = request.get_json(force=True) or {}
    turnos = [str(t) for t in dados.get("turnos", ["1", "2"]) if str(t) in ("1", "2")]
    ufs = [u for u in dados.get("ufs", []) if u in tse.UFS]
    if not turnos:
        abort(400, "Escolha ao menos um turno.")
    desc = f"TSE · {'+'.join(t + 'º' for t in turnos)} turno · {', '.join(ufs) if ufs else 'todas as UFs'}"
    return {"id": enfileirar(desc, {"tipo": "tse", "turnos": turnos, "ufs": ufs})}


# ---------------------------------------------------------------- upload em partes
uploads: dict[str, dict] = {}
_up_lock = threading.Lock()


@app.post("/api/upload/init")
def upload_init():
    dados = request.get_json(force=True) or {}
    nome = os.path.basename(str(dados.get("nome", "")))
    tamanho = int(dados.get("tamanho", 0))
    if not nome.lower().endswith((".zip", ".csv", ".logjez")):
        abort(400, "Envie um arquivo .zip, .csv ou .logjez.")
    if tamanho <= 0 or tamanho > MAX_UPLOAD:
        abort(400, f"Tamanho inválido (limite {MAX_UPLOAD/1e9:.0f} GB).")
    if shutil.disk_usage(TMP_DIR).free < tamanho * 1.1:
        abort(413, "Espaço em disco insuficiente no servidor.")
    uid = uuid.uuid4().hex
    caminho = os.path.join(TMP_DIR, f"{uid}_{nome}")
    open(caminho, "wb").close()
    turno = str(dados.get("turno", "")) if str(dados.get("turno", "")) in ("1", "2") else ""
    uf = str(dados.get("uf", "")).upper() if str(dados.get("uf", "")).upper() in logs.UFS else ""
    with _up_lock:
        uploads[uid] = {"nome": nome, "caminho": caminho, "tamanho": tamanho, "proximo": 0, "recebido": 0,
                        "turno": turno, "uf": uf}
    return {"id": uid}


@app.put("/api/upload/<uid>/<int:parte>")
def upload_parte(uid, parte):
    with _up_lock:
        up = uploads.get(uid)
    if not up:
        abort(404, "Envio não encontrado. Recomece.")
    if parte != up["proximo"]:
        if parte < up["proximo"]:  # reenvio de parte já gravada
            return {"recebido": up["recebido"]}
        abort(409, f"Parte fora de ordem: esperada {up['proximo']}.")
    dados = request.get_data(cache=False)
    if up["recebido"] + len(dados) > up["tamanho"]:
        abort(400, "Dados excedem o tamanho declarado.")
    with open(up["caminho"], "ab") as f:
        f.write(dados)
    up["recebido"] += len(dados)
    up["proximo"] += 1
    return {"recebido": up["recebido"]}


@app.post("/api/upload/<uid>/fim")
def upload_fim(uid):
    with _up_lock:
        up = uploads.pop(uid, None)
    if not up:
        abort(404, "Envio não encontrado.")
    if up["recebido"] != up["tamanho"]:
        os.remove(up["caminho"])
        abort(400, "Arquivo incompleto. Envie novamente.")
    return {"id": enfileirar(f"Arquivo · {up['nome']}", {"tipo": "arquivo", "caminho": up["caminho"], "nome": up["nome"],
                                                        "turno": up["turno"], "uf": up["uf"]})}


# ---------------------------------------------------------------- logs de urna: votos após as 17h
def base_logs():
    """Subconsulta das seções de log: de uma rodada (?rodada=N) ou a versão mais recente de cada seção."""
    rid = request.args.get("rodada", "").strip()
    if rid:
        if not rid.isdigit():
            abort(400, "Rodada inválida.")
        return "SELECT * FROM log_secoes WHERE rodada_id=?", (int(rid),)
    return ("""SELECT l.* FROM log_secoes l JOIN (
                 SELECT turno, uf, cd_municipio, zona, secao, MAX(rodada_id) r FROM log_secoes
                 GROUP BY turno, uf, cd_municipio, zona, secao) u
               ON l.turno=u.turno AND l.uf=u.uf AND l.cd_municipio=u.cd_municipio
              AND l.zona=u.zona AND l.secao=u.secao AND l.rodada_id=u.r""", ())


@app.get("/api/logs/resumo")
def logs_resumo():
    base, p = base_logs()
    por_uf = db.consultar(f"""
        SELECT turno, uf, COUNT(*) secoes, SUM(arquivos) arquivos, SUM(votos) votos,
               SUM(votos_apos_aj) votos_apos_aj, SUM(votos_apos_br) votos_apos_br,
               SUM(sem) sem, SUM(sem_apos_aj) sem_apos_aj, SUM(sem_apos_br) sem_apos_br
        FROM ({base}) b GROUP BY turno, uf ORDER BY turno, uf""", p)
    horas = {}
    for r in db.consultar(f"SELECT turno, uf, primeiro, ultimo FROM ({base}) b", p):
        h = horas.setdefault((r["turno"], r["uf"]), ([], []))
        h[0].append(r["primeiro"])
        h[1].append(r["ultimo"])
    bu = {(r["turno"], r["uf"]): r for r in db.consultar("""
        SELECT turno, uf, SUM(valor) valor, SUM(secoes) secoes FROM resultados
        WHERE coluna = (SELECT MIN(coluna) FROM resultados) GROUP BY turno, uf""")}
    for r in por_uf:
        prim, ult = horas.get((r["turno"], r["uf"]), ([], []))
        r["primeiro_mediana"] = logs.hhmm(logs.mediana_hora(prim))
        r["ultimo_mediana"] = logs.hhmm(logs.mediana_hora(ult))
        r["fuso"] = logs.FUSO_UF.get(r["uf"], 0)
        b = bu.get((r["turno"], r["uf"]))
        r["bu_sem"] = b["valor"] if b else None
        r["bu_secoes"] = b["secoes"] if b else None
    return jsonify(por_uf=por_uf)


@app.get("/api/logs/secoes")
def logs_secoes():
    base, p = base_logs()
    modo = "br" if request.args.get("modo") == "br" else "aj"
    linhas = db.consultar(f"""
        SELECT cd_municipio, zona, secao, arquivos, votos, votos_apos_{modo} votos_apos, sem, sem_apos_{modo} sem_apos,
               primeiro, ultimo FROM ({base}) b WHERE turno=? AND uf=?
        ORDER BY sem_apos_{modo} DESC, votos_apos_{modo} DESC LIMIT 300""",
                          (*p, request.args.get("turno", ""), request.args.get("uf", "")))
    nomes = {r["cd_municipio"]: r["nm_municipio"] for r in db.consultar(
        "SELECT DISTINCT cd_municipio, nm_municipio FROM resultados UNION SELECT DISTINCT cd_municipio, nm_municipio FROM rodada_resultados")}
    for r in linhas:
        r["nm_municipio"] = nomes.get(r["cd_municipio"], "")
        r["primeiro"], r["ultimo"] = logs.hhmm(r["primeiro"]), logs.hhmm(r["ultimo"])
    return jsonify(linhas)


@app.get("/api/logs/export.csv")
def logs_exportar():
    base, p = base_logs()
    linhas = db.consultar(f"""
        SELECT turno, uf, cd_municipio, zona, secao, arquivos, votos,
               votos_apos_aj votos_apos_17h_brasilia, sem sem_digital, sem_apos_aj sem_digital_apos_17h_brasilia,
               votos_apos_br votos_apos_17h_relogio_urna, sem_apos_br sem_digital_apos_17h_relogio_urna,
               primeiro, ultimo FROM ({base}) b ORDER BY turno, uf, cd_municipio, zona, secao""", p)
    for r in linhas:
        r["primeiro"], r["ultimo"] = logs.hhmm(r["primeiro"]), logs.hhmm(r["ultimo"])
    buf = io.StringIO()
    if linhas:
        w = csv.DictWriter(buf, fieldnames=list(linhas[0].keys()), delimiter=";")
        w.writeheader()
        w.writerows(linhas)
    sufixo = f"_rodada{request.args['rodada']}" if request.args.get("rodada") else ""
    return Response("\ufeff" + buf.getvalue(), mimetype="text/csv",
                    headers={"Content-Disposition": f"attachment; filename=logs_apos_17h_secoes{sufixo}.csv"})


@app.get("/api/logs/config")
def logs_config():
    return jsonify(padroes=padroes_log(), padrao=logs.PADROES_PADRAO, fuso=logs.FUSO_UF)


@app.post("/api/logs/config")
def logs_config_salvar():
    dados = request.get_json(force=True) or {}
    if dados.get("restaurar"):
        db.gravar_config("padroes_log", None)
    else:
        try:
            padroes = logs.validar_padroes(dados.get("padroes") or {})
        except logs.LogError as e:
            abort(400, str(e))
        db.gravar_config("padroes_log", json.dumps(padroes, ensure_ascii=False))
    return jsonify(padroes=padroes_log())


@app.get("/api/logs/diagnostico")
def logs_diagnostico():
    linhas = db.consultar("SELECT id, descricao, criado, diagnostico FROM tarefas "
                          "WHERE diagnostico IS NOT NULL ORDER BY id DESC LIMIT 1")
    if not linhas:
        return jsonify(None)
    r = linhas[0]
    r["diagnostico"] = json.loads(r["diagnostico"])
    return jsonify(r)


@app.get("/api/tse/logs")
def tse_logs_disponiveis():
    try:
        return jsonify(tse.listar_logs())
    except Exception as e:  # noqa: BLE001
        return jsonify(erro=str(e)), 502


@app.post("/api/tse/logs")
def tse_logs_baixar():
    dados = request.get_json(force=True) or {}
    turnos = [str(t) for t in dados.get("turnos", ["2"]) if str(t) in ("1", "2")]
    ufs = [u for u in dados.get("ufs", []) if u in tse.UFS]
    if not turnos:
        abort(400, "Escolha ao menos um turno.")
    desc = f"Logs TSE · {'+'.join(t + 'º' for t in turnos)} turno · {', '.join(ufs) if ufs else 'todas as UFs'}"
    return {"id": enfileirar(desc, {"tipo": "logs_tse", "turnos": turnos, "ufs": ufs})}


@app.post("/api/logs/url")
def logs_url():
    dados = request.get_json(force=True) or {}
    url = str(dados.get("url", "")).strip()
    u = urlparse(url)
    host = (u.hostname or "").lower()
    if u.scheme not in ("http", "https") or not any(host == h or host.endswith("." + h) for h in HOSTS_LOG):
        abort(400, "Informe um endereço do TSE (domínio tse.jus.br).")
    nome = os.path.basename(u.path) or "logs.zip"
    if not nome.lower().endswith((".zip", ".logjez")):
        abort(400, "O endereço deve apontar para um arquivo .zip ou .logjez.")
    turno = str(dados.get("turno", "")) if str(dados.get("turno", "")) in ("1", "2") else ""
    uf = str(dados.get("uf", "")).upper() if str(dados.get("uf", "")).upper() in logs.UFS else ""
    return {"id": enfileirar(f"Logs · {nome}", {"tipo": "logs_url", "url": url, "nome": nome, "turno": turno, "uf": uf})}


@app.errorhandler(400)
@app.errorhandler(404)
@app.errorhandler(409)
@app.errorhandler(413)
def erro_json(e):
    return jsonify(erro=getattr(e, "description", str(e))), e.code


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8000)), debug=False)

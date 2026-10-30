"""Apuração Sem Biometria — soma de eleitores liberados sem biometria nos Boletins de Urna (TSE)."""
import csv
import io
import os
import queue
import secrets
import shutil
import tempfile
import threading
import traceback
import uuid

from flask import Flask, Response, abort, jsonify, render_template, request

import db
import processor
import tse

app = Flask(__name__)
db.init()

SENHA = os.environ.get("APP_PASSWORD", "")
MAX_UPLOAD = float(os.environ.get("MAX_UPLOAD_GB", "6")) * 1e9
TMP_DIR = os.environ.get("TMP_DIR", os.path.join(tempfile.gettempdir(), "apuracao"))
os.makedirs(TMP_DIR, exist_ok=True)

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

    def salvar(linhas, origem):
        # a rodada só é criada quando há o primeiro resultado (tarefa com erro não gera rodada vazia)
        if rodada["id"] is None:
            rodada["id"] = db.nova_rodada(tid, job.get("descricao", ""))
        return db.salvar_resultados(linhas, origem, rodada["id"])

    if job["tipo"] == "arquivo":
        caminho = job["caminho"]
        try:
            cols, secoes = processor.ler_secoes(caminho, prog)
            pares = salvar(processor.agregar_por_municipio(cols, secoes), job["nome"])
            resumo.append(", ".join(f"{t}ºT {u}" for t, u in pares))
        finally:
            if os.path.exists(caminho):
                os.remove(caminho)
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
    por_id = {}
    for t in totais:
        por_id.setdefault(t.pop("rodada_id"), []).append(t)
    for r in lista:
        r["totais"] = por_id.get(r["id"], [])
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
    if not nome.lower().endswith((".zip", ".csv")):
        abort(400, "Envie um arquivo .zip ou .csv.")
    if tamanho <= 0 or tamanho > MAX_UPLOAD:
        abort(400, f"Tamanho inválido (limite {MAX_UPLOAD/1e9:.0f} GB).")
    if shutil.disk_usage(TMP_DIR).free < tamanho * 1.1:
        abort(413, "Espaço em disco insuficiente no servidor.")
    uid = uuid.uuid4().hex
    caminho = os.path.join(TMP_DIR, f"{uid}_{nome}")
    open(caminho, "wb").close()
    with _up_lock:
        uploads[uid] = {"nome": nome, "caminho": caminho, "tamanho": tamanho, "proximo": 0, "recebido": 0}
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
    return {"id": enfileirar(f"Arquivo · {up['nome']}", {"tipo": "arquivo", "caminho": up["caminho"], "nome": up["nome"]})}


@app.errorhandler(400)
@app.errorhandler(404)
@app.errorhandler(409)
@app.errorhandler(413)
def erro_json(e):
    return jsonify(erro=getattr(e, "description", str(e))), e.code


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8000)), debug=False)

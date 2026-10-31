"""Leitura dos logs das urnas (.logjez) para contar votos registrados após as 17h.

Cada .logjez é um arquivo 7-Zip com o log da urna (logd.dat): uma linha por evento,
com data e hora no início. Para cada eleitor o log registra, em sequência, a identificação,
as tentativas de leitura da digital (ou a liberação pelo procedimento alternativo) e,
no fim, o registro de que o voto foi computado.

O texto exato das mensagens muda entre versões do software da urna. Por isso os três
padrões usados aqui (início de eleitor, liberação sem digital e voto computado) são
expressões regulares configuráveis no painel, e cada processamento devolve as mensagens
mais frequentes encontradas para conferência.
"""
import io
import lzma
import os
import re
import shutil
import statistics
import tempfile
import zipfile
from collections import Counter

import py7zr

UFS = ("AC", "AL", "AM", "AP", "BA", "CE", "DF", "ES", "GO", "MA", "MG", "MS", "MT", "PA", "PB",
       "PE", "PI", "PR", "RJ", "RN", "RO", "RR", "RS", "SC", "SE", "SP", "TO", "ZZ")

# Diferença do horário local da UF em relação a Brasília (sem horário de verão).
# Observação: o oeste do Amazonas (UTC-5) e Fernando de Noronha (UTC-2) diferem da capital do estado.
FUSO_UF = {"AC": -2, "AM": -1, "RO": -1, "RR": -1, "MT": -1, "MS": -1}

PADROES_PADRAO = {
    "inicio": r"titulo digitado|cpf digitado|numero do titulo|identificacao do eleitor",
    "sem_digital": r"habilitacao manual|dado pessoal|ano de nascimento|sem biometria|sem digital|liberacao manual|codigo de liberacao",
    "computado": r"voto do eleitor foi computado|voto computado",
}

HORA_LIMITE = 17 * 3600  # 17h00 em segundos

RE_DATA_HORA = re.compile(r"(\d{2})/(\d{2})/(\d{4})[ \t]+(\d{2}):(\d{2}):(\d{2})")
RE_SECAO = re.compile(r"(\d{5})(\d{4})(\d{4})\.logjez$", re.I)
RE_UF = re.compile(r"(?:^|[^A-Z])(" + "|".join(UFS) + r")(?:[^A-Z]|$)")
RE_TURNO = re.compile(r"(?:^|[^0-9a-z])([12])\s*t(?:urno)?(?:[^a-z]|$)", re.I)
_SEM_ACENTO = str.maketrans("áàâãäéèêëíìîïóòôõöúùûüçÁÀÂÃÄÉÈÊËÍÌÎÏÓÒÔÕÖÚÙÛÜÇ",
                            "aaaaaeeeeiiiiooooouuuucaaaaaeeeeiiiiooooouuuuc")
MAGIC_7Z = b"7z\xbc\xaf\x27\x1c"


class LogError(Exception):
    pass


def normalizar(texto: str) -> str:
    return texto.translate(_SEM_ACENTO).lower()


def validar_padroes(padroes: dict) -> dict:
    saida = {}
    for chave in PADROES_PADRAO:
        valor = str(padroes.get(chave) or PADROES_PADRAO[chave]).strip()
        try:
            re.compile(valor)
        except re.error as e:
            raise LogError(f"Padrão '{chave}' inválido: {e}") from e
        saida[chave] = valor
    return saida


def limite_local(uf: str) -> int:
    """17h de Brasília convertidas para a hora local da UF. Exterior (ZZ) vota em hora local."""
    return HORA_LIMITE + FUSO_UF.get(uf, 0) * 3600


def texto_do_log(dados: bytes) -> str:
    """Extrai o texto do log de um .logjez (7z), de um .xz/.lzma ou de um texto puro."""
    if dados[:6] == MAGIC_7Z:
        with py7zr.SevenZipFile(io.BytesIO(dados), "r") as arq:
            conteudo = arq.readall() or {}
        nomes = sorted(conteudo)
        preferidos = [n for n in nomes if os.path.basename(n).lower().startswith("logd")] or nomes
        brutos = b"\n".join(conteudo[n].read() for n in preferidos)
    else:
        try:
            brutos = lzma.decompress(dados)
        except lzma.LZMAError:
            brutos = dados
    return brutos.decode("latin-1", errors="replace")


def _mensagem(linha: str) -> str:
    partes = linha.split("\t")
    msg = partes[4] if len(partes) >= 5 else RE_DATA_HORA.sub("", linha)
    return re.sub(r"\d+", "#", msg.strip())[:140]


def analisar_log(dados: bytes, padroes: dict, uf: str, coletar_mensagens: bool = False) -> dict:
    """Conta votos computados e liberações sem digital (total e após as 17h) em um log."""
    re_ini = re.compile(padroes["inicio"])
    re_sem = re.compile(padroes["sem_digital"])
    re_comp = re.compile(padroes["computado"])
    lim_aj, lim_br = limite_local(uf), HORA_LIMITE

    r = {"votos": 0, "votos_apos_aj": 0, "votos_apos_br": 0, "sem": 0, "sem_apos_aj": 0, "sem_apos_br": 0,
         "primeiro": None, "ultimo": None, "mensagens": Counter()}
    sem_digital = False
    for linha in texto_do_log(dados).splitlines():
        if not linha:
            continue
        n = normalizar(linha)
        if coletar_mensagens:
            r["mensagens"][normalizar(_mensagem(linha))] += 1
        if re_comp.search(n):
            m = RE_DATA_HORA.search(linha)
            seg = int(m.group(4)) * 3600 + int(m.group(5)) * 60 + int(m.group(6)) if m else None
            r["votos"] += 1
            if sem_digital:
                r["sem"] += 1
            if seg is not None:
                r["primeiro"] = seg if r["primeiro"] is None else min(r["primeiro"], seg)
                r["ultimo"] = seg if r["ultimo"] is None else max(r["ultimo"], seg)
                if seg >= lim_aj:
                    r["votos_apos_aj"] += 1
                    r["sem_apos_aj"] += sem_digital
                if seg >= lim_br:
                    r["votos_apos_br"] += 1
                    r["sem_apos_br"] += sem_digital
            sem_digital = False
            continue
        if re_ini.search(n):
            sem_digital = False
        if re_sem.search(n):
            sem_digital = True
    if not coletar_mensagens:
        r.pop("mensagens")
    return r


def _trabalho(args):
    """Função executada nos processos auxiliares."""
    caminho, dados, padroes, uf, coletar = args
    try:
        return caminho, analisar_log(dados, padroes, uf, coletar), None
    except Exception as e:  # noqa: BLE001
        return caminho, None, f"{type(e).__name__}: {e}"


def identificar_secao(caminho: str):
    """(cd_municipio, zona, secao) a partir do nome do arquivo; senão usa o próprio nome."""
    m = RE_SECAO.search(os.path.basename(caminho))
    if m:
        return m.group(1), m.group(2), m.group(3)
    return "", "", os.path.basename(caminho)


def detectar_uf(caminho: str):
    for parte in reversed(re.split(r"[\\/]", caminho.upper())):
        m = RE_UF.search(parte.replace(".LOGJEZ", "").replace(".ZIP", ""))
        if m:
            return m.group(1)
    return None


def detectar_turno(caminho: str):
    m = RE_TURNO.search(os.path.basename(caminho)) or RE_TURNO.search(caminho)
    return m.group(1) if m else None


def eh_arquivo_de_logs(caminho: str) -> bool:
    """True se o arquivo é um .logjez ou um ZIP com logs (diretos ou em ZIPs internos)."""
    if caminho.lower().endswith(".logjez"):
        return True
    if not zipfile.is_zipfile(caminho):
        return False
    with zipfile.ZipFile(caminho) as z:
        nomes = [n.lower() for n in z.namelist()]
    if any(n.endswith(".logjez") for n in nomes):
        return True
    if any(n.endswith(".csv") for n in nomes):
        return False
    return any(n.endswith(".zip") for n in nomes)


def iterar_logs(caminho: str, tmp_dir: str, rotulo: str = ""):
    """Gera (caminho_logico, bytes) para cada .logjez, abrindo ZIPs internos se houver."""
    rotulo = rotulo or os.path.basename(caminho)
    if not zipfile.is_zipfile(caminho):
        if caminho.lower().endswith(".logjez") or rotulo.lower().endswith(".logjez"):
            with open(caminho, "rb") as f:
                yield rotulo, f.read()
        return
    with zipfile.ZipFile(caminho) as z:
        for info in z.infolist():
            nome = info.filename
            if nome.lower().endswith(".logjez"):
                with z.open(info) as f:
                    yield f"{rotulo}/{nome}", f.read()
            elif nome.lower().endswith(".zip"):
                fd, interno = tempfile.mkstemp(suffix=".zip", dir=tmp_dir)
                try:
                    with os.fdopen(fd, "wb") as out, z.open(info) as f:
                        shutil.copyfileobj(f, out, 4 << 20)
                    yield from iterar_logs(interno, tmp_dir, f"{rotulo}/{nome}")
                finally:
                    os.remove(interno)


def mediana_hora(valores):
    v = [x for x in valores if x is not None]
    return int(statistics.median(v)) if v else None


def hhmm(seg):
    if seg is None:
        return ""
    return f"{seg // 3600:02d}:{seg % 3600 // 60:02d}"

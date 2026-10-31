"""Localiza e baixa no Portal de Dados Abertos do TSE os boletins de urna e os logs das urnas."""
import os
import re

import requests

DATASET = os.environ.get("TSE_DATASET", "resultados-2026-boletim-de-urna")
API = "https://dadosabertos.tse.jus.br/api/3/action/package_show"
PADRAO = re.compile(r"bweb_(\d)t_([A-Z]{2})_.*\.zip$", re.I)
UFS = ["AC", "AL", "AM", "AP", "BA", "CE", "DF", "ES", "GO", "MA", "MG", "MS", "MT", "PA", "PB",
       "PE", "PI", "PR", "RJ", "RN", "RO", "RR", "RS", "SC", "SE", "SP", "TO", "ZZ"]

_sessao = requests.Session()
_sessao.headers["User-Agent"] = "apuracao-biometria/1.0"


def listar_arquivos():
    """Lista [{turno, uf, url, nome}] dos ZIPs bweb disponíveis no conjunto de dados."""
    r = _sessao.get(API, params={"id": DATASET}, timeout=60)
    if r.status_code == 404:
        raise RuntimeError(f"Conjunto '{DATASET}' não encontrado no Portal de Dados Abertos. "
                           "Confira se o TSE já publicou ou ajuste a variável TSE_DATASET.")
    r.raise_for_status()
    saida = []
    for res in r.json()["result"].get("resources", []):
        url = res.get("url", "")
        m = PADRAO.search(url.rsplit("/", 1)[-1])
        if m:
            saida.append({"turno": m.group(1), "uf": m.group(2).upper(), "url": url,
                          "nome": url.rsplit("/", 1)[-1]})
    return sorted(saida, key=lambda a: (a["turno"], a["uf"]))


def baixar(url, destino, progresso=None):
    """Baixa em streaming para `destino`. progresso(fracao, texto)."""
    with _sessao.get(url, stream=True, timeout=(30, 300)) as r:
        r.raise_for_status()
        total = int(r.headers.get("Content-Length") or 0)
        feito = 0
        with open(destino, "wb") as f:
            for parte in r.iter_content(4 << 20):
                f.write(parte)
                feito += len(parte)
                if progresso:
                    mb = feito / 1e6
                    progresso(feito / total if total else 0,
                              f"baixando {mb:,.0f} MB".replace(",", ".") + (f" de {total/1e6:,.0f} MB".replace(",", ".") if total else ""))
    return destino


# ---------------------------------------------------------------- logs das urnas
ANO = os.environ.get("TSE_ANO", "2026")
# Os logs das urnas de votação são publicados junto com BU, RDV e assinaturas no conjunto
# "Arquivos transmitidos para totalização" (ZIPs bu_imgbu_logjez_rdv_vscmr_<ano>_<n>t_<UF>.zip).
LOG_DATASET_PADRAO = f"resultados-{ANO}-arquivos-transmitidos-para-totalizacao"
LOG_DATASETS = [d.strip() for d in os.environ.get("TSE_LOG_DATASET", "").split(",") if d.strip()]
SEARCH = "https://dadosabertos.tse.jus.br/api/3/action/package_search"
# logs que NÃO são das urnas de votação (sistema de preparação/GEDAI)
_EXCLUIR_DATASET = ("gedai", "preparacao")
_SEM_ACENTO = str.maketrans("áàâãäéèêíìîóòôõöúùûüçÁÀÂÃÄÉÈÊÍÌÎÓÒÔÕÖÚÙÛÜÇ",
                            "aaaaaeeeiiiooooouuuucAAAAAEEEIIIOOOOOUUUUC")
_RE_UF = re.compile(r"(?:^|[^A-Z])(" + "|".join(UFS) + r")(?:[^A-Z]|$)")


def _norm(t):
    return (t or "").translate(_SEM_ACENTO).lower()


def _turno_de(texto):
    t = _norm(texto)
    if re.search(r"segundo turno|(?:^|[^0-9a-z])2\s*(?:o|º)?\s*[-_ ]?t(?:urno)?(?:[^a-z]|$)", t):
        return "2"
    if re.search(r"primeiro turno|(?:^|[^0-9a-z])1\s*(?:o|º)?\s*[-_ ]?t(?:urno)?(?:[^a-z]|$)", t):
        return "1"
    return None


def _uf_de(*textos):
    for texto in textos:
        if not texto:
            continue
        base = texto.rsplit("/", 1)[-1].upper()
        inicio = re.match(r"\s*([A-Z]{2})\s*[-–]", texto.upper())  # "SP - Logs de urna ..."
        if inicio and inicio.group(1) in UFS:
            return inicio.group(1)
        m = _RE_UF.search(re.sub(r"\.(ZIP|LOGJEZ)$", "", base))
        if m:
            return m.group(1)
    return None


def _eh_dataset_de_logs(pacote):
    nome = _norm(pacote.get("name", "") + " " + pacote.get("title", ""))
    if ANO not in nome or not ("log" in nome or "arquivos-transmitidos" in nome or "arquivos transmitidos" in nome):
        return False
    return not any(x in nome for x in _EXCLUIR_DATASET)


def buscar_datasets_logs():
    """Pacotes (com recursos) dos logs das urnas: TSE_LOG_DATASET, senão o conjunto padrão
    "Arquivos transmitidos para totalização" e, se ele não existir, uma pesquisa no catálogo."""
    pacotes = []
    for nome in (LOG_DATASETS or [LOG_DATASET_PADRAO]):
        r = _sessao.get(API, params={"id": nome}, timeout=60)
        if r.status_code == 404:
            if LOG_DATASETS:
                raise RuntimeError(f"Conjunto '{nome}' (TSE_LOG_DATASET) não encontrado no Portal de Dados Abertos.")
            continue
        r.raise_for_status()
        pacotes.append(r.json()["result"])
    if pacotes:
        return pacotes
    vistos = set()
    for q in (f"arquivos transmitidos totalizacao {ANO}", f"logs urna {ANO}", f"log urna eletronica {ANO}"):
        r = _sessao.get(SEARCH, params={"q": q, "rows": 100}, timeout=60)
        r.raise_for_status()
        for p in r.json()["result"].get("results", []):
            if p["name"] not in vistos and _eh_dataset_de_logs(p):
                vistos.add(p["name"])
                pacotes.append(p)
    return pacotes


def listar_logs():
    """Lista [{turno, uf, url, nome, dataset}] dos ZIPs de logs das urnas por UF e turno."""
    pacotes = buscar_datasets_logs()
    if not pacotes:
        raise RuntimeError(f"Nenhum conjunto de logs de urna de {ANO} encontrado no Portal de Dados Abertos. "
                           "Se o TSE já publicou com outro nome, informe-o na variável TSE_LOG_DATASET "
                           "ou cole o link do arquivo em 'Baixar logs pelo endereço'.")
    saida, vistos = [], set()
    for p in pacotes:
        turno_pacote = _turno_de(p.get("name", "")) or _turno_de(p.get("title", ""))
        for res in p.get("resources", []):
            url = res.get("url", "")
            nome_res = res.get("name", "") or ""
            arq = url.rsplit("/", 1)[-1]
            if not arq.lower().endswith((".zip", ".logjez")) or "hash" in _norm(nome_res) or "sha" in arq.lower():
                continue
            # ZIPs combinados (bu_imgbu_..._rdv) só servem se incluírem logs
            if "logjez" not in arq.lower() and "log" not in _norm(nome_res) and "transmitidos" not in _norm(nome_res + p.get("name", "")):
                continue
            uf = _uf_de(nome_res, arq)
            turno = _turno_de(nome_res) or _turno_de(arq) or turno_pacote
            if not uf or not turno or url in vistos:
                continue
            vistos.add(url)
            saida.append({"turno": turno, "uf": uf, "url": url, "nome": arq, "dataset": p.get("name", ""),
                          "tamanho": res.get("size")})
    if not saida:
        nomes = ", ".join(p.get("name", "") for p in pacotes)
        raise RuntimeError(f"Os conjuntos encontrados ({nomes}) não têm arquivos de logs por UF e turno reconhecíveis.")
    return sorted(saida, key=lambda a: (a["turno"], a["uf"]))

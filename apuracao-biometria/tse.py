"""Localiza e baixa os arquivos de Boletim de Urna no Portal de Dados Abertos do TSE."""
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

"""Busca automática dos logs no Portal de Dados Abertos, com o catálogo do TSE simulado."""
import io
import os
import sys
import tempfile
import unittest
import zipfile
from unittest import mock

TMP = tempfile.mkdtemp()
os.environ.setdefault("DATA_DIR", TMP)
os.environ.setdefault("TMP_DIR", os.path.join(TMP, "tmp"))
os.environ["LOG_WORKERS"] = "0"
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import tse  # noqa: E402
from test_logs import LOG_AC, LOG_DF  # noqa: E402

CDN = "https://cdn.tse.jus.br/estatistica/sead/eleicoes/eleicoes2026/arqurnatot"
PACOTES = [
    {"name": "resultados-2026-logs-do-sistema-de-preparacao-das-urnas-eletronicas-gedai-2-turno", "title": "GEDAI",
     "resources": [{"name": "DF - Logs GEDAI", "url": f"{CDN}/gedai_DF.zip"}]},
    {"name": "resultados-2026-logs-de-urna", "title": "Resultados 2026 - Logs de urna",
     "resources": [
         {"name": "DF - Logs de urna - Segundo turno - 28.10.2026", "url": f"{CDN}/logs_DF_2t.zip", "size": 1000},
         {"name": "DF - Hash SHA2 - Segundo turno", "url": f"{CDN}/logs_DF_2t.zip.sha512"},
         {"name": "AC - Logs de urna - Segundo turno - 28.10.2026", "url": f"{CDN}/logs_AC_2t.zip"},
         {"name": "AC - Logs de urna - Primeiro turno - 07.10.2026", "url": f"{CDN}/logs_AC_1t.zip"},
         {"name": "Leia-me", "url": f"{CDN}/leiame.pdf"},
     ]},
]


def zip_com(nome, conteudo):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr(nome, conteudo)
    return buf.getvalue()


ARQUIVOS = {
    f"{CDN}/logs_DF_2t.zip": zip_com("o00407-9701200010010.logjez", LOG_DF),
    f"{CDN}/logs_AC_2t.zip": zip_com("o00407-0139200080001.logjez", LOG_AC),
}


class Resp:
    def __init__(self, json=None, conteudo=b"", status=200):
        self._json, self.conteudo, self.status_code = json, conteudo, status
        self.headers = {"Content-Length": str(len(conteudo))}

    def json(self):
        return self._json

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def iter_content(self, n):
        for i in range(0, len(self.conteudo), n):
            yield self.conteudo[i:i + n]

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def falso_get(url, params=None, stream=False, timeout=None):
    if url == tse.SEARCH:
        return Resp({"result": {"results": PACOTES}})
    if url in ARQUIVOS:
        return Resp(conteudo=ARQUIVOS[url])
    return Resp(status=404)


class TesteBuscaLogsTSE(unittest.TestCase):
    def test_lista_filtra_e_identifica(self):
        with mock.patch.object(tse._sessao, "get", side_effect=falso_get):
            lista = tse.listar_logs()
        self.assertEqual([(a["turno"], a["uf"], a["nome"]) for a in lista],
                         [("1", "AC", "logs_AC_1t.zip"), ("2", "AC", "logs_AC_2t.zip"), ("2", "DF", "logs_DF_2t.zip")])
        self.assertTrue(all(a["dataset"] == "resultados-2026-logs-de-urna" for a in lista))

    def test_sem_conjunto(self):
        with mock.patch.object(tse._sessao, "get", return_value=Resp({"result": {"results": [PACOTES[0]]}})):
            with self.assertRaises(RuntimeError) as ctx:
                tse.listar_logs()
        self.assertIn("TSE_LOG_DATASET", str(ctx.exception))

    def test_busca_e_processa(self):
        import app as appmod
        appmod.LOG_WORKERS = 0
        c = appmod.app.test_client()
        with mock.patch.object(tse._sessao, "get", side_effect=falso_get):
            self.assertEqual(len(c.get("/api/tse/logs").get_json()), 3)
            r = c.post("/api/tse/logs", json={"turnos": ["2"], "ufs": []})
            self.assertEqual(r.status_code, 200)
            appmod.fila.join()
        tarefa = c.get("/api/tarefas").get_json()[0]
        self.assertEqual(tarefa["status"], "concluída", tarefa)
        self.assertIn("2ºT AC", tarefa["mensagem"])
        self.assertIn("2ºT DF", tarefa["mensagem"])
        rid = next(r["id"] for r in c.get("/api/rodadas").get_json() if r["tarefa_id"] == tarefa["id"])
        por_uf = {r["uf"]: r for r in c.get(f"/api/logs/resumo?rodada={rid}").get_json()["por_uf"]}
        self.assertEqual((por_uf["DF"]["sem_apos_aj"], por_uf["AC"]["sem_apos_aj"], por_uf["AC"]["sem_apos_br"]), (1, 1, 0))
        self.assertEqual(c.post("/api/tse/logs", json={"turnos": []}).status_code, 400)


if __name__ == "__main__":
    unittest.main()

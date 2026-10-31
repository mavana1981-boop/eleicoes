import io
import os
import sys
import tempfile
import unittest
import zipfile

TMP = tempfile.mkdtemp()
os.environ.setdefault("DATA_DIR", TMP)
os.environ.setdefault("TMP_DIR", os.path.join(TMP, "tmp"))
os.environ["LOG_WORKERS"] = "0"
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import py7zr  # noqa: E402

import logs  # noqa: E402

DIA = "25/10/2026"


def linha(hora, msg):
    return f"{DIA} {hora}\tINFO\t67305985\tVOTA\t{msg}\t9F3A21C0"


def eleitor(hora_fim, sem_digital=False, vota=True):
    ls = [linha("08:00:00", "Título digitado pelo mesário"),
          linha("08:00:01", "Solicita digital. Tentativa [1] de [4]")]
    if sem_digital:
        ls.append(linha("08:00:05", "Solicitação de dado pessoal do eleitor para habilitação manual"))
    if vota:
        ls += [linha("08:00:09", "Eleitor foi habilitado"),
               linha("08:00:20", "Voto confirmado para [Presidente]"),
               linha(hora_fim, "O voto do eleitor foi computado")]
    return ls


def logjez(linhas):
    buf = io.BytesIO()
    with py7zr.SevenZipFile(buf, "w") as arq:
        arq.writestr("\n".join(linhas).encode("latin-1"), "logd.dat")
    return buf.getvalue()


# DF: corte 17:00. Votos: 16:59:50 (digital), 17:00:10 (sem digital), desistente sem digital, 17:05 (digital)
LOG_DF = logjez(
    [linha("07:00:00", "Início das operações do logd")]
    + eleitor("16:59:50")
    + eleitor("17:00:10", sem_digital=True)
    + eleitor("", sem_digital=True, vota=False)
    + eleitor("17:05:00")
)
# AC: relógio local, 17h de Brasília = 15h no Acre. Voto sem digital às 15:30 conta só no modo ajustado.
LOG_AC = logjez(eleitor("06:10:00") + eleitor("15:30:00", sem_digital=True))


class TesteLogs(unittest.TestCase):
    def test_analise_df(self):
        r = logs.analisar_log(LOG_DF, logs.PADROES_PADRAO, "DF", coletar_mensagens=True)
        self.assertEqual((r["votos"], r["sem"]), (3, 1))  # desistente não conta e não contamina o próximo
        self.assertEqual((r["votos_apos_aj"], r["sem_apos_aj"]), (2, 1))
        self.assertEqual((r["votos_apos_br"], r["sem_apos_br"]), (2, 1))
        self.assertEqual(logs.hhmm(r["primeiro"]), "16:59")
        self.assertIn("o voto do eleitor foi computado", r["mensagens"])
        self.assertIn("solicita digital. tentativa [#] de [#]", r["mensagens"])

    def test_fuso_acre(self):
        r = logs.analisar_log(LOG_AC, logs.PADROES_PADRAO, "AC")
        self.assertEqual((r["votos"], r["sem"]), (2, 1))
        self.assertEqual((r["votos_apos_aj"], r["sem_apos_aj"]), (1, 1))  # 15:30 local = 17:30 Brasília
        self.assertEqual((r["votos_apos_br"], r["sem_apos_br"]), (0, 0))

    def test_padrao_invalido(self):
        with self.assertRaises(logs.LogError):
            logs.validar_padroes({"computado": "(abc"})

    def test_deteccao(self):
        self.assertEqual(logs.identificar_secao("x/o00407-9701200010010.logjez"), ("97012", "0001", "0010"))
        self.assertEqual(logs.detectar_uf("logs_2t_DF.zip/o00407-9701200010010.logjez"), "DF")
        self.assertEqual(logs.detectar_uf("logs.zip/PA/arquivo.logjez"), "PA")
        self.assertIsNone(logs.detectar_uf("PARTE.zip/arquivo.logjez"))
        self.assertEqual(logs.detectar_turno("logs_2t_DF.zip"), "2")
        self.assertIsNone(logs.detectar_turno("o00407-9701200010010.logjez"))

    def test_fluxo_completo(self):
        import app as appmod
        appmod.LOG_WORKERS = 0
        caminho = os.path.join(TMP, "logs_2t_DF.zip")
        interno = io.BytesIO()
        with zipfile.ZipFile(interno, "w") as zi:
            zi.writestr("o00407-0139200080001.logjez", LOG_AC)
        with zipfile.ZipFile(caminho, "w") as z:
            z.writestr("o00407-9701200010010.logjez", LOG_DF)
            z.writestr("AC/logs_AC.zip", interno.getvalue())
        self.assertTrue(logs.eh_arquivo_de_logs(caminho))
        dados = open(caminho, "rb").read()

        c = appmod.app.test_client()
        uid = c.post("/api/upload/init", json={"nome": "logs_2t_DF.zip", "tamanho": len(dados)}).get_json()["id"]
        c.put(f"/api/upload/{uid}/0", data=dados)
        self.assertEqual(c.post(f"/api/upload/{uid}/fim").status_code, 200)
        appmod.fila.join()
        tarefa = c.get("/api/tarefas").get_json()[0]
        self.assertEqual(tarefa["status"], "concluída", tarefa)

        por_uf = {r["uf"]: r for r in c.get("/api/logs/resumo").get_json()["por_uf"]}
        self.assertEqual(set(por_uf), {"AC", "DF"})
        self.assertEqual((por_uf["DF"]["sem_apos_aj"], por_uf["DF"]["votos_apos_aj"]), (1, 2))
        self.assertEqual((por_uf["AC"]["sem_apos_aj"], por_uf["AC"]["sem_apos_br"]), (1, 0))
        self.assertEqual(por_uf["AC"]["fuso"], -2)

        sec = c.get("/api/logs/secoes?turno=2&uf=DF").get_json()
        self.assertEqual((sec[0]["zona"], sec[0]["secao"], sec[0]["sem_apos"]), ("0001", "0010", 1))
        self.assertIn("sem_digital_apos_17h_brasilia", c.get("/api/logs/export.csv").get_data(as_text=True))

        diag = c.get("/api/logs/diagnostico").get_json()["diagnostico"]
        self.assertEqual(diag["logs"], 2)
        rotulos = {m: tags for m, _c, tags in diag["mensagens"]}
        self.assertIn("computado", rotulos["o voto do eleitor foi computado"])

        rid = c.get("/api/rodadas").get_json()[0]["id"]
        self.assertEqual(c.get(f"/api/logs/resumo?rodada={rid}").get_json()["por_uf"][0]["turno"], "2")

        # padrões configuráveis
        self.assertEqual(c.post("/api/logs/config", json={"padroes": {"computado": "(x"}}).status_code, 400)
        r = c.post("/api/logs/config", json={"padroes": {"computado": "voto .* computado"}}).get_json()
        self.assertEqual(r["padroes"]["computado"], "voto .* computado")
        c.post("/api/logs/config", json={"restaurar": True})

        # endereço fora do TSE é recusado
        self.assertEqual(c.post("/api/logs/url", json={"url": "https://exemplo.com/logs.zip"}).status_code, 400)

    def test_processos_paralelos(self):
        import app as appmod
        appmod.LOG_WORKERS = 2
        try:
            gen = (((f"a{i}.logjez", LOG_DF, logs.PADROES_PADRAO, "DF", False), i) for i in range(5))
            res = list(appmod._mapear(gen))
        finally:
            appmod.LOG_WORKERS = 0
        self.assertEqual([m for m, _ in res], list(range(5)))
        self.assertTrue(all(r[2] is None and r[1]["sem_apos_aj"] == 1 for _, r in res))


if __name__ == "__main__":
    unittest.main()

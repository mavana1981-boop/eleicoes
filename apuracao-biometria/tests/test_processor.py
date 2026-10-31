import io
import json
import os
import sys
import tempfile
import unittest
import zipfile

TMP = tempfile.mkdtemp()
os.environ["DATA_DIR"] = TMP
os.environ["TMP_DIR"] = os.path.join(TMP, "tmp")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import processor  # noqa: E402

CAB = ('"NR_TURNO";"SG_UF";"CD_MUNICIPIO";"NM_MUNICIPIO";"NR_ZONA";"NR_SECAO";"DS_CARGO_PERGUNTA";'
       '"NR_VOTAVEL";"QT_VOTOS";"QT_APTOS";"QT_COMPARECIMENTO";"QT_ELEI_BIOM_NAO_HABILITADO"')


def boletim(secoes):
    """secoes: [(municipio, nome, zona, secao, comparecimento, sem_bio)] → bytes CSV latin-1"""
    linhas = [CAB]
    for cd, nm, zona, sec, comp, bio in secoes:
        for cand in ("13", "22", "95", "96"):
            linhas.append(f'"2";"DF";"{cd}";"{nm}";"{zona}";"{sec}";"Presidente";"{cand}";"10";"300";"{comp}";"{bio}"')
    return "\n".join(linhas).encode("latin-1")


DADOS = [("97012", "BRASÍLIA", "1", "10", 250, 12),
         ("97012", "BRASÍLIA", "1", "11", 200, 8),
         ("97012", "BRASÍLIA", "2", "10", 180, 0)]


class TesteProcessador(unittest.TestCase):
    def setUp(self):
        self.zip = os.path.join(TMP, "bweb_2t_DF_teste.zip")
        with zipfile.ZipFile(self.zip, "w", zipfile.ZIP_DEFLATED) as z:
            z.writestr("bweb_2t_DF.csv", boletim(DADOS))
            z.writestr("leiame.pdf", b"%PDF")

    def test_soma_uma_vez_por_secao(self):
        cols, secoes = processor.ler_secoes(self.zip)
        self.assertEqual(cols, ["QT_ELEI_BIOM_NAO_HABILITADO"])
        self.assertEqual(len(secoes), 3)  # zona 2 seção 10 é diferente de zona 1 seção 10
        linhas = processor.agregar_por_municipio(cols, secoes)
        self.assertEqual(len(linhas), 1)
        l = linhas[0]
        self.assertEqual((l["valor"], l["comparecimento"], l["secoes"], l["nm_municipio"]), (20, 630, 3, "BRASÍLIA"))

    def test_csv_sem_coluna_biometria(self):
        caminho = os.path.join(TMP, "ruim.csv")
        with open(caminho, "wb") as f:
            f.write(b'"NR_TURNO";"SG_UF";"CD_MUNICIPIO";"NR_ZONA";"NR_SECAO";"QT_VOTOS"\n"1";"AC";"1";"1";"1";"5"\n')
        with self.assertRaises(processor.ProcessingError):
            processor.ler_secoes(caminho)

    def test_arquivo_detalhe_votacao(self):
        caminho = os.path.join(TMP, "detalhe_votacao_secao_2026_AP.csv")
        with open(caminho, "wb") as f:
            f.write(b'"NR_TURNO";"SG_UF";"CD_MUNICIPIO";"NR_ZONA";"NR_SECAO";"QT_COMPARECIMENTO";"QT_VOTOS_NOMINAIS"\n'
                    b'"2";"AP";"6050";"2";"10";"300";"280"\n')
        with self.assertRaises(processor.ProcessingError) as ctx:
            processor.ler_secoes(caminho)
        self.assertIn("Boletim de Urna", str(ctx.exception))

    def test_fluxo_completo_upload(self):
        import app as appmod
        c = appmod.app.test_client()
        dados = open(self.zip, "rb").read()
        r = c.post("/api/upload/init", json={"nome": "bweb_2t_DF_teste.zip", "tamanho": len(dados)})
        uid = r.get_json()["id"]
        meio = len(dados) // 2
        self.assertEqual(c.put(f"/api/upload/{uid}/0", data=dados[:meio]).status_code, 200)
        self.assertEqual(c.put(f"/api/upload/{uid}/2", data=b"x").status_code, 409)  # fora de ordem
        self.assertEqual(c.put(f"/api/upload/{uid}/1", data=dados[meio:]).status_code, 200)
        self.assertEqual(c.post(f"/api/upload/{uid}/fim").status_code, 200)
        appmod.fila.join()
        tarefa = c.get("/api/tarefas").get_json()[0]
        self.assertEqual(tarefa["status"], "concluída", tarefa)
        res = c.get("/api/resumo").get_json()
        self.assertEqual(res["por_turno"][0]["valor"], 20)
        csvtxt = c.get("/api/export.csv?nivel=municipio").get_data(as_text=True)
        self.assertIn("BRASÍLIA", csvtxt)
        self.assertEqual(c.get("/").status_code, 200)

        # cada processamento vira uma rodada arquivada
        rodadas = [r for r in c.get("/api/rodadas").get_json() if r["tarefa_id"] == tarefa["id"]]
        self.assertEqual(len(rodadas), 1)
        rid = rodadas[0]["id"]
        self.assertEqual(rodadas[0]["totais"][0]["valor"], 20)
        self.assertIn(f"Rodada #{rid}", tarefa["mensagem"])

        # apagar os resultados atuais não apaga o arquivo
        c.post("/api/limpar")
        self.assertEqual(c.get("/api/resumo").get_json()["por_turno"], [])
        arq = c.get(f"/api/resumo?rodada={rid}").get_json()
        self.assertEqual(arq["por_turno"][0]["valor"], 20)
        mun = c.get(f"/api/municipios?rodada={rid}&turno=2&uf=DF").get_json()
        self.assertEqual(mun[0]["valor"], 20)
        self.assertIn(f"rodada{rid}", c.get(f"/api/export.csv?rodada={rid}").headers["Content-Disposition"])

        c.delete(f"/api/rodadas/{rid}")
        self.assertNotIn(rid, [r["id"] for r in c.get("/api/rodadas").get_json()])


if __name__ == "__main__":
    unittest.main()

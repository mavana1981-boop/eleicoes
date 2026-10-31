"""Leitura dos arquivos de Boletim de Urna (bweb) e soma das liberações sem biometria.

Cada seção eleitoral aparece no CSV em várias linhas (uma por candidato/cargo).
Para cada seção guardamos um único valor por coluna (o máximo encontrado) e depois
somamos por turno, UF e município.
"""
import csv
import io
import os
import zipfile
from collections import defaultdict

csv.field_size_limit(10_000_000)

CHAVE = ("NR_TURNO", "SG_UF", "CD_MUNICIPIO", "NR_ZONA", "NR_SECAO")
MARCADORES_BIO = ("BIOM", "NASC", "LIB", "HABIL")


class ProcessingError(Exception):
    pass


def eh_coluna_biometria(nome: str) -> bool:
    n = nome.upper()
    return n.startswith("QT_") and any(m in n for m in MARCADORES_BIO)


def _int(valor: str) -> int:
    try:
        v = int(valor)
    except (TypeError, ValueError):
        return 0
    return v if v > 0 else 0


class _ContadorBytes(io.RawIOBase):
    """Embrulha um arquivo binário contando os bytes lidos (para a barra de progresso)."""

    def __init__(self, f):
        self.f = f
        self.lidos = 0

    def readable(self):
        return True

    def readinto(self, b):
        dados = self.f.read(len(b))
        n = len(dados)
        b[:n] = dados
        self.lidos += n
        return n

    def close(self):
        try:
            self.f.close()
        finally:
            super().close()


def _abrir_csvs(caminho):
    """Gera (nome, contador, tamanho, stream_texto) para cada CSV do ZIP ou para o CSV avulso."""
    if zipfile.is_zipfile(caminho):
        with zipfile.ZipFile(caminho) as z:
            entradas = [i for i in z.infolist() if i.filename.lower().endswith(".csv")]
            if not entradas:
                raise ProcessingError("O ZIP não contém nenhum arquivo .csv.")
            for info in entradas:
                cont = _ContadorBytes(z.open(info))
                texto = io.TextIOWrapper(io.BufferedReader(cont, 1 << 20), encoding="latin-1", newline="")
                try:
                    yield info.filename, cont, info.file_size, texto
                finally:
                    texto.close()
    else:
        cont = _ContadorBytes(open(caminho, "rb"))
        texto = io.TextIOWrapper(io.BufferedReader(cont, 1 << 20), encoding="latin-1", newline="")
        try:
            yield os.path.basename(caminho), cont, os.path.getsize(caminho), texto
        finally:
            texto.close()


def ler_secoes(caminho, progresso=None):
    """Lê um ZIP/CSV bweb e devolve (colunas_bio, secoes).

    secoes: dict chave_secao -> [nm_municipio, comparecimento, valor_col1, valor_col2, ...]
    progresso: callback(fracao 0..1, texto)
    """
    colunas_bio = None
    secoes = {}
    for nome, cont, tamanho, texto in _abrir_csvs(caminho):
        leitor = csv.reader(texto, delimiter=";", quotechar='"')
        try:
            cab = [c.strip().upper() for c in next(leitor)]
        except StopIteration:
            continue
        idx = {c: i for i, c in enumerate(cab)}
        faltando = [c for c in CHAVE if c not in idx]
        if faltando:
            raise ProcessingError(f"{nome}: colunas ausentes {faltando}. Este não parece um arquivo de Boletim de Urna.")
        bio = [c for c in cab if eh_coluna_biometria(c)]
        if not bio:
            if "QT_VOTOS_NOMINAIS" in idx or "DT_RECEBIMENTO_BU_HOR_TSE" in idx or nome.lower().startswith("detalhe_votacao"):
                raise ProcessingError(
                    f"{nome} é o arquivo 'Detalhe da votação por seção', que não informa a biometria. "
                    "Use o Boletim de Urna: conjunto 'resultados-2026-boletim-de-urna' no Portal de Dados Abertos, "
                    "arquivos bweb_1t_UF_….zip ou bweb_2t_UF_….zip (ou o botão 'Buscar no TSE').")
            raise ProcessingError(f"{nome}: nenhuma coluna de liberação sem biometria encontrada. Colunas: {', '.join(cab)}")
        if colunas_bio is None:
            colunas_bio = bio
        elif bio != colunas_bio:
            colunas_bio = list(dict.fromkeys(colunas_bio + bio))

        pos_bio = [(colunas_bio.index(c), idx[c]) for c in bio]
        i_chave = [idx[c] for c in CHAVE]
        i_comp = idx.get("QT_COMPARECIMENTO")
        i_nome = idx.get("NM_MUNICIPIO")
        n_col = len(cab)
        largura = 2 + len(colunas_bio)

        for n, linha in enumerate(leitor, 1):
            if len(linha) < n_col:
                continue
            chave = tuple(linha[i] for i in i_chave)
            s = secoes.get(chave)
            if s is None:
                s = [linha[i_nome] if i_nome is not None else "", 0] + [0] * (largura - 2)
                secoes[chave] = s
            elif len(s) < largura:
                s.extend([0] * (largura - len(s)))
            if i_comp is not None:
                v = _int(linha[i_comp])
                if v > s[1]:
                    s[1] = v
            for pos, i in pos_bio:
                v = _int(linha[i])
                if v > s[2 + pos]:
                    s[2 + pos] = v
            if progresso and n % 200_000 == 0:
                progresso(min(cont.lidos / tamanho, 1.0) if tamanho else 0, f"{nome}: {n:,} linhas".replace(",", "."))
    if colunas_bio is None:
        raise ProcessingError("Nenhum dado encontrado no arquivo.")
    if progresso:
        progresso(1.0, f"{len(secoes):,} seções lidas".replace(",", "."))
    return colunas_bio, secoes


def agregar_por_municipio(colunas_bio, secoes):
    """Soma por (turno, uf, cd_municipio). Devolve lista de dicts."""
    acc = defaultdict(lambda: {"nm": "", "secoes": 0, "comp": 0, "vals": [0] * len(colunas_bio)})
    for (turno, uf, cd_mun, _zona, _secao), s in secoes.items():
        a = acc[(turno, uf, cd_mun)]
        a["nm"] = a["nm"] or s[0]
        a["secoes"] += 1
        a["comp"] += s[1]
        for j in range(len(colunas_bio)):
            a["vals"][j] += s[2 + j] if 2 + j < len(s) else 0
    linhas = []
    for (turno, uf, cd_mun), a in acc.items():
        for j, col in enumerate(colunas_bio):
            linhas.append({
                "turno": str(turno), "uf": uf, "cd_municipio": cd_mun, "nm_municipio": a["nm"],
                "coluna": col, "secoes": a["secoes"], "comparecimento": a["comp"], "valor": a["vals"][j],
            })
    return linhas

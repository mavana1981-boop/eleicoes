"""Persistência em SQLite (resultados atuais, arquivo de rodadas e histórico de tarefas)."""
import os
import sqlite3
import threading
from datetime import datetime, timezone

DATA_DIR = os.environ.get("DATA_DIR", os.path.join(os.path.dirname(os.path.abspath(__file__)), "data"))
os.makedirs(DATA_DIR, exist_ok=True)
DB_PATH = os.path.join(DATA_DIR, "apuracao.db")

_lock = threading.Lock()
_conn = sqlite3.connect(DB_PATH, check_same_thread=False)
_conn.row_factory = sqlite3.Row
_conn.execute("PRAGMA journal_mode=WAL")


def agora():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def init():
    with _lock, _conn:
        _conn.executescript("""
        CREATE TABLE IF NOT EXISTS resultados (
            turno TEXT, uf TEXT, cd_municipio TEXT, nm_municipio TEXT, coluna TEXT,
            secoes INTEGER, comparecimento INTEGER, valor INTEGER, origem TEXT, atualizado TEXT
        );
        CREATE INDEX IF NOT EXISTS ix_res ON resultados (turno, uf, coluna);
        CREATE TABLE IF NOT EXISTS rodadas (
            id INTEGER PRIMARY KEY AUTOINCREMENT, tarefa_id INTEGER, descricao TEXT, criado TEXT
        );
        CREATE TABLE IF NOT EXISTS rodada_resultados (
            rodada_id INTEGER, turno TEXT, uf TEXT, cd_municipio TEXT, nm_municipio TEXT, coluna TEXT,
            secoes INTEGER, comparecimento INTEGER, valor INTEGER, origem TEXT, atualizado TEXT
        );
        CREATE INDEX IF NOT EXISTS ix_rod ON rodada_resultados (rodada_id, turno, uf);
        CREATE TABLE IF NOT EXISTS log_secoes (
            rodada_id INTEGER, turno TEXT, uf TEXT, cd_municipio TEXT, zona TEXT, secao TEXT,
            arquivos INTEGER, votos INTEGER, votos_apos_aj INTEGER, votos_apos_br INTEGER,
            sem INTEGER, sem_apos_aj INTEGER, sem_apos_br INTEGER,
            primeiro INTEGER, ultimo INTEGER, origem TEXT, atualizado TEXT
        );
        CREATE INDEX IF NOT EXISTS ix_log ON log_secoes (turno, uf, cd_municipio, zona, secao, rodada_id);
        CREATE INDEX IF NOT EXISTS ix_log_rod ON log_secoes (rodada_id);
        CREATE TABLE IF NOT EXISTS config (chave TEXT PRIMARY KEY, valor TEXT);
        CREATE TABLE IF NOT EXISTS tarefas (
            id INTEGER PRIMARY KEY AUTOINCREMENT, descricao TEXT, status TEXT,
            progresso REAL DEFAULT 0, mensagem TEXT DEFAULT '', criado TEXT, atualizado TEXT
        );
        """)
        # tarefas interrompidas por reinício do servidor
        _conn.execute("UPDATE tarefas SET status='erro', mensagem='Interrompida pelo reinício do servidor' "
                      "WHERE status IN ('na fila','executando')")
        colunas = [r[1] for r in _conn.execute("PRAGMA table_info(tarefas)")]
        if "diagnostico" not in colunas:
            _conn.execute("ALTER TABLE tarefas ADD COLUMN diagnostico TEXT")
        # resultados existentes antes do arquivamento viram a primeira rodada
        sem_rodadas = _conn.execute("SELECT COUNT(*) FROM rodadas").fetchone()[0] == 0
        tem_dados = _conn.execute("SELECT COUNT(*) FROM resultados").fetchone()[0] > 0
        if sem_rodadas and tem_dados:
            rid = _conn.execute("INSERT INTO rodadas (tarefa_id, descricao, criado) VALUES (NULL, ?, ?)",
                                ("Resultados anteriores ao arquivamento", agora())).lastrowid
            _conn.execute("INSERT INTO rodada_resultados SELECT ?, * FROM resultados", (rid,))


def salvar_resultados(linhas, origem, rodada_id=None):
    """Substitui os dados atuais dos pares (turno, UF) presentes em `linhas`
    e, se `rodada_id` for informado, arquiva uma cópia na rodada."""
    pares = {(l["turno"], l["uf"]) for l in linhas}
    ts = agora()
    with _lock, _conn:
        for turno, uf in pares:
            _conn.execute("DELETE FROM resultados WHERE turno=? AND uf=?", (turno, uf))
        _conn.executemany(
            "INSERT INTO resultados VALUES (?,?,?,?,?,?,?,?,?,?)",
            [(l["turno"], l["uf"], l["cd_municipio"], l["nm_municipio"], l["coluna"],
              l["secoes"], l["comparecimento"], l["valor"], origem, ts) for l in linhas],
        )
        if rodada_id is not None:
            _conn.executemany(
                "INSERT INTO rodada_resultados VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                [(rodada_id, l["turno"], l["uf"], l["cd_municipio"], l["nm_municipio"], l["coluna"],
                  l["secoes"], l["comparecimento"], l["valor"], origem, ts) for l in linhas],
            )
    return sorted(pares)


def nova_rodada(tarefa_id, descricao):
    return executar("INSERT INTO rodadas (tarefa_id, descricao, criado) VALUES (?,?,?)",
                    (tarefa_id, descricao, agora()))


def excluir_rodada(rid):
    with _lock, _conn:
        _conn.execute("DELETE FROM rodada_resultados WHERE rodada_id=?", (rid,))
        _conn.execute("DELETE FROM log_secoes WHERE rodada_id=?", (rid,))
        _conn.execute("DELETE FROM rodadas WHERE id=?", (rid,))


def consultar(sql, params=()):
    with _lock:
        return [dict(r) for r in _conn.execute(sql, params).fetchall()]


def executar(sql, params=()):
    with _lock, _conn:
        cur = _conn.execute(sql, params)
        return cur.lastrowid


def nova_tarefa(descricao):
    ts = agora()
    return executar("INSERT INTO tarefas (descricao,status,criado,atualizado) VALUES (?,?,?,?)",
                    (descricao, "na fila", ts, ts))


def atualizar_tarefa(tid, **campos):
    campos["atualizado"] = agora()
    sets = ", ".join(f"{k}=?" for k in campos)
    executar(f"UPDATE tarefas SET {sets} WHERE id=?", (*campos.values(), tid))


def salvar_logs(rodada_id, linhas):
    ts = agora()
    with _lock, _conn:
        _conn.executemany(
            "INSERT INTO log_secoes VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            [(rodada_id, l["turno"], l["uf"], l["cd_municipio"], l["zona"], l["secao"], l["arquivos"],
              l["votos"], l["votos_apos_aj"], l["votos_apos_br"], l["sem"], l["sem_apos_aj"], l["sem_apos_br"],
              l["primeiro"], l["ultimo"], l["origem"], ts) for l in linhas],
        )


def ler_config(chave, padrao=None):
    linhas = consultar("SELECT valor FROM config WHERE chave=?", (chave,))
    return linhas[0]["valor"] if linhas else padrao


def gravar_config(chave, valor):
    if valor is None:
        executar("DELETE FROM config WHERE chave=?", (chave,))
    else:
        executar("INSERT INTO config (chave, valor) VALUES (?, ?) "
                 "ON CONFLICT(chave) DO UPDATE SET valor=excluded.valor", (chave, valor))

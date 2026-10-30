"""Persistência em SQLite (resultados agregados e histórico de tarefas)."""
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
        CREATE TABLE IF NOT EXISTS tarefas (
            id INTEGER PRIMARY KEY AUTOINCREMENT, descricao TEXT, status TEXT,
            progresso REAL DEFAULT 0, mensagem TEXT DEFAULT '', criado TEXT, atualizado TEXT
        );
        """)
        # tarefas interrompidas por reinício do servidor
        _conn.execute("UPDATE tarefas SET status='erro', mensagem='Interrompida pelo reinício do servidor' "
                      "WHERE status IN ('na fila','executando')")


def salvar_resultados(linhas, origem):
    """Substitui os dados dos pares (turno, UF) presentes em `linhas`."""
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
    return sorted(pares)


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

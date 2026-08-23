"""Esquema e helpers de acesso ao SQLite.

Convenções (ver PROMPT original):
- preço sempre em centavos, inteiro, nunca float.
- timestamps sempre em UTC, formato ISO 8601 ('YYYY-MM-DDTHH:MM:SS+00:00').
  O fuso de exibição (America/Fortaleza) é aplicado só na hora de renderizar.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Optional

SCHEMA = """
CREATE TABLE IF NOT EXISTS consultas (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts_utc TEXT NOT NULL,
    tipo TEXT NOT NULL,
    origem TEXT NOT NULL,
    destino TEXT NOT NULL,
    data_ida TEXT,
    data_volta TEXT,
    ok INTEGER NOT NULL,
    erro TEXT,
    fonte TEXT NOT NULL,
    google_price_level TEXT,
    google_typical_low INTEGER,
    google_typical_high INTEGER
);

CREATE TABLE IF NOT EXISTS ofertas (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    consulta_id INTEGER NOT NULL REFERENCES consultas(id),
    cia TEXT,
    preco_centavos INTEGER NOT NULL,
    moeda TEXT NOT NULL DEFAULT 'BRL',
    is_best INTEGER NOT NULL DEFAULT 0,
    aeroporto_origem TEXT,
    aeroporto_destino TEXT,
    saida_ts TEXT,
    chegada_ts TEXT,
    duracao_min INTEGER,
    n_paradas INTEGER,
    conexoes_json TEXT,
    aeronave TEXT
);

CREATE TABLE IF NOT EXISTS historico_google (
    ts_utc TEXT NOT NULL UNIQUE,
    preco_centavos INTEGER NOT NULL,
    importado_em TEXT NOT NULL
);

-- Não fazia parte do schema original do prompt: necessária pra anti-spam
-- ('no máximo 1 alerta imediato a cada 4h') sobreviver entre execuções,
-- já que cada rodada do GitHub Actions roda num container novo sem
-- memória do processo anterior.
CREATE TABLE IF NOT EXISTS notificacoes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts_utc TEXT NOT NULL,
    tipo TEXT NOT NULL  -- 'rotina' | 'novo_minimo' | 'queda_significativa' | 'falha'
);

CREATE INDEX IF NOT EXISTS idx_consultas_ts ON consultas(ts_utc);
CREATE INDEX IF NOT EXISTS idx_consultas_tipo ON consultas(tipo);
CREATE INDEX IF NOT EXISTS idx_ofertas_consulta ON ofertas(consulta_id);
CREATE INDEX IF NOT EXISTS idx_historico_ts ON historico_google(ts_utc);
"""


def connect(db_path: str | Path) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA foreign_keys = ON")
    conn.row_factory = sqlite3.Row
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    conn.commit()


def insert_consulta(conn: sqlite3.Connection, row: dict) -> int:
    """row precisa ter: ts_utc, tipo, origem, destino, data_ida, data_volta,
    ok, erro, fonte, google_price_level, google_typical_low, google_typical_high
    """
    cur = conn.execute(
        """
        INSERT INTO consultas
            (ts_utc, tipo, origem, destino, data_ida, data_volta,
             ok, erro, fonte, google_price_level, google_typical_low, google_typical_high)
        VALUES
            (:ts_utc, :tipo, :origem, :destino, :data_ida, :data_volta,
             :ok, :erro, :fonte, :google_price_level, :google_typical_low, :google_typical_high)
        """,
        row,
    )
    return cur.lastrowid


def insert_oferta(conn: sqlite3.Connection, consulta_id: int, row: dict) -> int:
    """row precisa ter: cia, preco_centavos, moeda, is_best, aeroporto_origem,
    aeroporto_destino, saida_ts, chegada_ts, duracao_min, n_paradas,
    conexoes_json, aeronave
    """
    payload = {"consulta_id": consulta_id, **row}
    cur = conn.execute(
        """
        INSERT INTO ofertas
            (consulta_id, cia, preco_centavos, moeda, is_best,
             aeroporto_origem, aeroporto_destino, saida_ts, chegada_ts,
             duracao_min, n_paradas, conexoes_json, aeronave)
        VALUES
            (:consulta_id, :cia, :preco_centavos, :moeda, :is_best,
             :aeroporto_origem, :aeroporto_destino, :saida_ts, :chegada_ts,
             :duracao_min, :n_paradas, :conexoes_json, :aeronave)
        """,
        payload,
    )
    return cur.lastrowid


def insert_notificacao(conn: sqlite3.Connection, ts_utc: str, tipo: str) -> None:
    conn.execute("INSERT INTO notificacoes (ts_utc, tipo) VALUES (?, ?)", (ts_utc, tipo))


def ultima_notificacao_imediata(conn: sqlite3.Connection) -> Optional[sqlite3.Row]:
    return conn.execute(
        """
        SELECT ts_utc, tipo FROM notificacoes
        WHERE tipo IN ('novo_minimo', 'queda_significativa')
        ORDER BY ts_utc DESC LIMIT 1
        """
    ).fetchone()


def insert_historico_google(conn: sqlite3.Connection, ts_utc: str, preco_centavos: int, importado_em: str) -> None:
    """Upsert por ts_utc: reimportações semanais sobrepõem a janela de ~60
    dias da anterior, então precisa substituir, não duplicar."""
    conn.execute(
        """
        INSERT INTO historico_google (ts_utc, preco_centavos, importado_em)
        VALUES (?, ?, ?)
        ON CONFLICT(ts_utc) DO UPDATE SET
            preco_centavos = excluded.preco_centavos,
            importado_em = excluded.importado_em
        """,
        (ts_utc, preco_centavos, importado_em),
    )

"""Leituras do banco que alimentam o módulo de métricas — as duas séries
paralelas (mais_barato_da_rodada, itinerario_referencia) e os candidatos
sugeridos na primeira execução. Nada aqui é armazenado, só calculado na
hora a partir de consultas/ofertas (ver métricas.py)."""

from __future__ import annotations

import sqlite3
from datetime import datetime
from typing import Optional

from emailer import FUSO_EXIBICAO
from metrics import PricePoint


def serie_mais_barato_da_rodada(conn: sqlite3.Connection, tipo: str = "round_trip_1012") -> list[PricePoint]:
    """Uma amostra por rodada ok: a oferta mais barata daquela consulta,
    pode trocar de cia/horário entre rodadas."""
    rows = conn.execute(
        """
        SELECT c.ts_utc AS ts, MIN(o.preco_centavos) AS preco
        FROM consultas c JOIN ofertas o ON o.consulta_id = c.id
        WHERE c.tipo = ? AND c.ok = 1
        GROUP BY c.id
        ORDER BY c.ts_utc
        """,
        (tipo,),
    ).fetchall()
    return [PricePoint(ts_utc=datetime.fromisoformat(r["ts"]), preco_centavos=r["preco"]) for r in rows]


def serie_itinerario_referencia(
    conn: sqlite3.Connection, *, tipo: str, cia: str, hora_min: str, hora_max: str
) -> list[PricePoint]:
    """Uma amostra por rodada ok em que a cia fixada operou dentro da janela
    de horário de saída (local, America/Fortaleza) fixada. Se a cia não
    aparecer numa rodada dentro da janela, aquela rodada simplesmente não
    contribui ponto nenhum pra essa série."""
    rows = conn.execute(
        """
        SELECT c.id AS consulta_id, c.ts_utc AS ts, o.preco_centavos, o.saida_ts
        FROM consultas c JOIN ofertas o ON o.consulta_id = c.id
        WHERE c.tipo = ? AND c.ok = 1 AND o.cia = ? AND o.saida_ts IS NOT NULL
        ORDER BY c.ts_utc
        """,
        (tipo, cia),
    ).fetchall()

    melhor_por_consulta: dict[int, PricePoint] = {}
    for r in rows:
        hora_local = datetime.fromisoformat(r["saida_ts"]).astimezone(FUSO_EXIBICAO).strftime("%H:%M")
        if not (hora_min <= hora_local <= hora_max):
            continue
        ponto_atual = melhor_por_consulta.get(r["consulta_id"])
        if ponto_atual is None or r["preco_centavos"] < ponto_atual.preco_centavos:
            melhor_por_consulta[r["consulta_id"]] = PricePoint(
                ts_utc=datetime.fromisoformat(r["ts"]), preco_centavos=r["preco_centavos"]
            )
    return sorted(melhor_por_consulta.values(), key=lambda p: p.ts_utc)


def candidatos_itinerario_referencia(
    conn: sqlite3.Connection, *, tipo: str = "round_trip_1012", top_n: int = 6
) -> list[dict]:
    """Pra 'na primeira execução, sugira candidatos': olha a rodada ok mais
    recente daquele tipo e lista os itinerários (cia + horário de saída da
    ida) distintos mais baratos, pra você escolher qual fixar."""
    consulta = conn.execute(
        "SELECT id, ts_utc FROM consultas WHERE tipo = ? AND ok = 1 ORDER BY ts_utc DESC LIMIT 1",
        (tipo,),
    ).fetchone()
    if not consulta:
        return []

    rows = conn.execute(
        """
        SELECT cia, saida_ts, chegada_ts, duracao_min, n_paradas, preco_centavos
        FROM ofertas WHERE consulta_id = ? AND saida_ts IS NOT NULL
        ORDER BY preco_centavos ASC
        """,
        (consulta["id"],),
    ).fetchall()

    vistos = set()
    candidatos = []
    for r in rows:
        hora_local = datetime.fromisoformat(r["saida_ts"]).astimezone(FUSO_EXIBICAO).strftime("%H:%M")
        chave = (r["cia"], hora_local)
        if chave in vistos:
            continue
        vistos.add(chave)
        candidatos.append(
            {
                "cia": r["cia"],
                "saida_hora_local": hora_local,
                "duracao_min": r["duracao_min"],
                "n_paradas": r["n_paradas"],
                "preco_centavos": r["preco_centavos"],
            }
        )
        if len(candidatos) >= top_n:
            break
    return candidatos

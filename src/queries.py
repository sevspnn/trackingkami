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


def serie_mais_barato_da_rodada(conn: sqlite3.Connection, rota: str, tipo: str = "round_trip_principal") -> list[PricePoint]:
    """Uma amostra por rodada ok: a oferta mais barata daquela consulta,
    pode trocar de cia/horário entre rodadas."""
    rows = conn.execute(
        """
        SELECT c.ts_utc AS ts, MIN(o.preco_centavos) AS preco
        FROM consultas c JOIN ofertas o ON o.consulta_id = c.id
        WHERE c.rota = ? AND c.tipo = ? AND c.ok = 1
        GROUP BY c.id
        ORDER BY c.ts_utc
        """,
        (rota, tipo),
    ).fetchall()
    return [PricePoint(ts_utc=datetime.fromisoformat(r["ts"]), preco_centavos=r["preco"]) for r in rows]


def serie_dois_trechos(
    conn: sqlite3.Connection, rota: str, prefixo_ida: str = "trecho_ida_", prefixo_volta: str = "trecho_volta_"
) -> list[PricePoint]:
    """Série do modo dois trechos: uma amostra por rodada = (ida mais
    barata entre as datas de ida) + (volta mais barata entre as datas de
    volta). Todas as consultas de uma rodada compartilham o mesmo ts_utc,
    então é por ele que a rodada é identificada. Rodada sem nenhum trecho
    de ida ok (ou nenhum de volta) não gera ponto."""
    rows = conn.execute(
        """
        SELECT c.ts_utc AS ts,
               MIN(CASE WHEN c.tipo LIKE ? || '%' THEN o.preco_centavos END) AS ida,
               MIN(CASE WHEN c.tipo LIKE ? || '%' THEN o.preco_centavos END) AS volta
        FROM consultas c JOIN ofertas o ON o.consulta_id = c.id
        WHERE c.rota = ? AND c.ok = 1
        GROUP BY c.ts_utc
        HAVING ida IS NOT NULL AND volta IS NOT NULL
        ORDER BY c.ts_utc
        """,
        (prefixo_ida, prefixo_volta, rota),
    ).fetchall()
    return [PricePoint(ts_utc=datetime.fromisoformat(r["ts"]), preco_centavos=r["ida"] + r["volta"]) for r in rows]


def serie_itinerario_referencia(
    conn: sqlite3.Connection, *, rota: str, tipo: str, cia: str, hora_min: str, hora_max: str
) -> list[PricePoint]:
    """Uma amostra por rodada ok em que a cia fixada operou dentro da janela
    de horário de saída (local, America/Fortaleza) fixada. Se a cia não
    aparecer numa rodada dentro da janela, aquela rodada simplesmente não
    contribui ponto nenhum pra essa série."""
    rows = conn.execute(
        """
        SELECT c.id AS consulta_id, c.ts_utc AS ts, o.preco_centavos, o.saida_ts
        FROM consultas c JOIN ofertas o ON o.consulta_id = c.id
        WHERE c.rota = ? AND c.tipo = ? AND c.ok = 1 AND o.cia = ? AND o.saida_ts IS NOT NULL
        ORDER BY c.ts_utc
        """,
        (rota, tipo, cia),
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
    conn: sqlite3.Connection, *, rota: str, tipo: str = "round_trip_principal", top_n: int = 6
) -> list[dict]:
    """Pra 'na primeira execução, sugira candidatos': olha a rodada ok mais
    recente daquele tipo e lista os itinerários (cia + horário de saída da
    ida) distintos mais baratos, pra você escolher qual fixar."""
    consulta = conn.execute(
        "SELECT id, ts_utc FROM consultas WHERE rota = ? AND tipo = ? AND ok = 1 ORDER BY ts_utc DESC LIMIT 1",
        (rota, tipo),
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

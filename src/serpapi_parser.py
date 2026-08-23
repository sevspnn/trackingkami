"""Converte a resposta crua do SerpApi (best_flights + other_flights) em
ParsedOferta — usado só quando o fast-flights falha numa rodada (captcha/
bloqueio) e caímos pro SerpApi como fallback pontual daquela rodada.

Schema real (ver ETAPA 6), bem mais rico que o do fast-flights:
- cada item de best_flights/other_flights tem 'flights': lista de segmentos
  (1 segmento = voo direto, 2+ = com conexão)
- cada segmento tem departure_airport/arrival_airport {id, time, name},
  duration (min, int), airplane, airline, flight_number
- 'layovers': lista de conexões com id/name/duration — dá pra preencher
  conexoes_json de verdade (o fast-flights não expõe isso)
- 'total_duration' (min, int), 'price' (int, já em reais, sem símbolo)
- horário no formato 'YYYY-MM-DD HH:MM', sem timezone explícito (mesma
  convenção BRT fixa do fast-flights — ver parser.py)
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Optional

from parser import ParsedOferta

BRT = timezone(timedelta(hours=-3))


def _parse_ts(texto: str) -> Optional[datetime]:
    """'2026-10-08 13:00' -> aware datetime em UTC (assume BRT na origem)."""
    if not texto:
        return None
    try:
        naive = datetime.strptime(texto, "%Y-%m-%d %H:%M")
    except ValueError:
        return None
    return naive.replace(tzinfo=BRT).astimezone(timezone.utc)


def _parse_itinerario(item: dict, is_best: bool, moeda: str) -> Optional[ParsedOferta]:
    segmentos = item.get("flights") or []
    if not segmentos:
        return None

    primeiro, ultimo = segmentos[0], segmentos[-1]
    preco = item.get("price")
    if preco is None:
        return None

    layovers = item.get("layovers") or []
    conexoes = [
        {"aeroporto": lv.get("id"), "nome": lv.get("name"), "duracao_min": lv.get("duration")}
        for lv in layovers
    ]
    aeronaves = sorted({s.get("airplane") for s in segmentos if s.get("airplane")})
    cias = sorted({s.get("airline") for s in segmentos if s.get("airline")})

    return ParsedOferta(
        cia=" + ".join(cias) if cias else None,
        preco_centavos=round(preco * 100),
        moeda=moeda,
        is_best=is_best,
        aeroporto_origem=primeiro.get("departure_airport", {}).get("id"),
        aeroporto_destino=ultimo.get("arrival_airport", {}).get("id"),
        saida_ts=_parse_ts(primeiro.get("departure_airport", {}).get("time")).isoformat()
        if _parse_ts(primeiro.get("departure_airport", {}).get("time"))
        else None,
        chegada_ts=_parse_ts(ultimo.get("arrival_airport", {}).get("time")).isoformat()
        if _parse_ts(ultimo.get("arrival_airport", {}).get("time"))
        else None,
        duracao_min=item.get("total_duration"),
        n_paradas=len(segmentos) - 1,
        conexoes_json=json.dumps(conexoes, ensure_ascii=False) if conexoes else None,
        aeronave=" + ".join(aeronaves) if aeronaves else None,
    )


def parse_serpapi_result(raw: dict, *, moeda: str = "BRL") -> list[ParsedOferta]:
    """Levanta ValueError se não achar nenhum itinerário — mesma semântica
    de 'zero ofertas é falha' do parser.py."""
    itens = [(it, True) for it in raw.get("best_flights") or []] + [
        (it, False) for it in raw.get("other_flights") or []
    ]
    ofertas = []
    for item, is_best in itens:
        oferta = _parse_itinerario(item, is_best, moeda)
        if oferta is not None:
            ofertas.append(oferta)

    if not ofertas:
        raise ValueError("zero ofertas no fallback SerpApi — tratar como falha da rodada")

    return ofertas

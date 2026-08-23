"""Define as 6 consultas do projeto e executa cada uma com retry/backoff no
fast-flights e fallback pro SerpApi (só naquela rodada) se esgotar as
tentativas. Ver ETAPA 1 (schema real do fast-flights) e ETAPA 6 (schema
real do SerpApi) pra contexto das decisões de parsing.
"""

from __future__ import annotations

import time
from typing import Optional

from fast_flights import FlightData, Passengers, TFSData, get_flights_from_filter

import config
import parser as parser_mod
import serpapi_client
import serpapi_parser


def _serpapi_airport(codigo: str) -> str:
    """SerpApi não aceita o city code BHZ (ver ETAPA 6) — traduz pro
    aeroporto de verdade. NAT passa direto."""
    return serpapi_client.AEROPORTO_DESTINO_ANCORA if codigo == config.DESTINO else codigo


def construir_queries() -> list[dict]:
    queries = [
        {
            "tipo": "round_trip_1012",
            "trip": "round-trip",
            "legs": [(config.DATA_IDA, config.ORIGEM, config.DESTINO), (config.DATA_VOLTA, config.DESTINO, config.ORIGEM)],
            "reference_date": config.DATA_IDA,
            "aeroporto_origem": config.ORIGEM,
            "aeroporto_destino": config.DESTINO,
            "data_ida": config.DATA_IDA,
            "data_volta": config.DATA_VOLTA,
        },
        {
            "tipo": "one_way_ida",
            "trip": "one-way",
            "legs": [(config.DATA_IDA, config.ORIGEM, config.DESTINO)],
            "reference_date": config.DATA_IDA,
            "aeroporto_origem": config.ORIGEM,
            "aeroporto_destino": config.DESTINO,
            "data_ida": config.DATA_IDA,
            "data_volta": None,
        },
        {
            "tipo": "one_way_volta_1012",
            "trip": "one-way",
            "legs": [(config.DATA_VOLTA, config.DESTINO, config.ORIGEM)],
            "reference_date": config.DATA_VOLTA,
            "aeroporto_origem": config.DESTINO,
            "aeroporto_destino": config.ORIGEM,
            "data_ida": None,
            "data_volta": config.DATA_VOLTA,
        },
    ]
    if config.VARIANTE_FLEXIVEL_ATIVA:
        queries += [
            {
                "tipo": "round_trip_1013",
                "trip": "round-trip",
                "legs": [
                    (config.DATA_IDA, config.ORIGEM, config.DESTINO),
                    (config.DATA_VOLTA_FLEXIVEL, config.DESTINO, config.ORIGEM),
                ],
                "reference_date": config.DATA_IDA,
                "aeroporto_origem": config.ORIGEM,
                "aeroporto_destino": config.DESTINO,
                "data_ida": config.DATA_IDA,
                "data_volta": config.DATA_VOLTA_FLEXIVEL,
            },
            {
                "tipo": "one_way_volta_1013",
                "trip": "one-way",
                "legs": [(config.DATA_VOLTA_FLEXIVEL, config.DESTINO, config.ORIGEM)],
                "reference_date": config.DATA_VOLTA_FLEXIVEL,
                "aeroporto_origem": config.DESTINO,
                "aeroporto_destino": config.ORIGEM,
                "data_ida": None,
                "data_volta": config.DATA_VOLTA_FLEXIVEL,
            },
        ]
    return queries


def _tentar_fast_flights(spec: dict) -> list[parser_mod.ParsedOferta]:
    flight_data = [FlightData(date=d, from_airport=o, to_airport=t) for d, o, t in spec["legs"]]
    filt = TFSData.from_interface(
        flight_data=flight_data,
        trip=spec["trip"],
        passengers=Passengers(adults=config.PASSAGEIROS_ADULTOS),
        seat=config.CLASSE,
    )
    result = get_flights_from_filter(filt, currency=config.MOEDA, mode="common")
    ofertas = parser_mod.parse_result(
        result,
        reference_date=spec["reference_date"],
        aeroporto_origem=spec["aeroporto_origem"],
        aeroporto_destino=spec["aeroporto_destino"],
        moeda=config.MOEDA,
    )
    return ofertas, result.current_price


def _tentar_serpapi_fallback(spec: dict, serpapi_key: str) -> tuple[list[parser_mod.ParsedOferta], Optional[str]]:
    origem = _serpapi_airport(spec["legs"][0][1])
    destino = _serpapi_airport(spec["legs"][0][2])
    data_ida = spec["legs"][0][0]
    data_volta = spec["legs"][1][0] if len(spec["legs"]) > 1 else None

    raw = serpapi_client.buscar_raw(
        api_key=serpapi_key, origem=origem, destino=destino, data_ida=data_ida, data_volta=data_volta
    )
    ofertas = serpapi_parser.parse_serpapi_result(raw, moeda=config.MOEDA)
    price_level = (raw.get("price_insights") or {}).get("price_level")
    return ofertas, price_level


def executar_consulta(spec: dict, *, serpapi_key: Optional[str] = None) -> dict:
    """Roda uma consulta com retry+backoff no fast-flights; se esgotar,
    tenta SerpApi só nessa rodada (se houver chave). Nunca levanta exceção
    — sempre devolve um dict com 'ok' indicando sucesso ou falha."""
    erros = []
    for tentativa in range(1, config.MAX_TENTATIVAS_FAST_FLIGHTS + 1):
        try:
            ofertas, price_level = _tentar_fast_flights(spec)
            return {
                "ok": True,
                "fonte": "fast_flights",
                "ofertas": ofertas,
                "google_price_level": price_level,
                "erro": None,
            }
        except Exception as e:
            erros.append(f"tentativa {tentativa}: {e}")
            if tentativa < config.MAX_TENTATIVAS_FAST_FLIGHTS:
                time.sleep(config.BACKOFF_BASE_SEGUNDOS * (2 ** (tentativa - 1)))

    erro_fast_flights = "; ".join(erros)

    if serpapi_key:
        try:
            ofertas, price_level = _tentar_serpapi_fallback(spec, serpapi_key)
            return {
                "ok": True,
                "fonte": "serpapi_fallback",
                "ofertas": ofertas,
                "google_price_level": price_level,
                "erro": f"fast-flights falhou ({erro_fast_flights}); usado fallback SerpApi",
            }
        except Exception as e:
            return {
                "ok": False,
                "fonte": "nenhuma",
                "ofertas": [],
                "google_price_level": None,
                "erro": f"fast-flights falhou ({erro_fast_flights}); fallback SerpApi também falhou ({e})",
            }

    return {
        "ok": False,
        "fonte": "nenhuma",
        "ofertas": [],
        "google_price_level": None,
        "erro": f"fast-flights falhou ({erro_fast_flights}); sem SERPAPI_KEY pra fallback",
    }

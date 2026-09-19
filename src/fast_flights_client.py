"""Define as consultas de uma rota e executa cada uma com retry/backoff no
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
from config import RotaConfig

# Únicos códigos de cidade (não-aeroporto) conhecidos que o fast-flights
# aceita mas o SerpApi não — precisa traduzir pro aeroporto de verdade só
# nesses casos (ver ETAPA 6). MCZ e SLZ já são aeroportos, passam direto.
_TRADUCAO_SERPAPI = {"BHZ": "CNF"}


def _serpapi_airport(codigo: str) -> str:
    return _TRADUCAO_SERPAPI.get(codigo, codigo)


def construir_queries(rota: RotaConfig) -> list[dict]:
    queries = [
        {
            "tipo": "round_trip_principal",
            "trip": "round-trip",
            "legs": [(rota.data_ida, rota.origem, rota.destino), (rota.data_volta, rota.destino, rota.origem)],
            "reference_date": rota.data_ida,
            "aeroporto_origem": rota.origem,
            "aeroporto_destino": rota.destino,
            "data_ida": rota.data_ida,
            "data_volta": rota.data_volta,
        },
        {
            "tipo": "one_way_ida",
            "trip": "one-way",
            "legs": [(rota.data_ida, rota.origem, rota.destino)],
            "reference_date": rota.data_ida,
            "aeroporto_origem": rota.origem,
            "aeroporto_destino": rota.destino,
            "data_ida": rota.data_ida,
            "data_volta": None,
        },
        {
            "tipo": "one_way_volta_principal",
            "trip": "one-way",
            "legs": [(rota.data_volta, rota.destino, rota.origem)],
            "reference_date": rota.data_volta,
            "aeroporto_origem": rota.destino,
            "aeroporto_destino": rota.origem,
            "data_ida": None,
            "data_volta": rota.data_volta,
        },
    ]
    if rota.variante_flexivel_ativa and rota.data_volta_flexivel:
        queries += [
            {
                "tipo": "round_trip_flexivel",
                "trip": "round-trip",
                "legs": [
                    (rota.data_ida, rota.origem, rota.destino),
                    (rota.data_volta_flexivel, rota.destino, rota.origem),
                ],
                "reference_date": rota.data_ida,
                "aeroporto_origem": rota.origem,
                "aeroporto_destino": rota.destino,
                "data_ida": rota.data_ida,
                "data_volta": rota.data_volta_flexivel,
            },
            {
                "tipo": "one_way_volta_flexivel",
                "trip": "one-way",
                "legs": [(rota.data_volta_flexivel, rota.destino, rota.origem)],
                "reference_date": rota.data_volta_flexivel,
                "aeroporto_origem": rota.destino,
                "aeroporto_destino": rota.origem,
                "data_ida": None,
                "data_volta": rota.data_volta_flexivel,
            },
        ]
    return queries


def _tentar_fast_flights(spec: dict) -> tuple[list[parser_mod.ParsedOferta], str]:
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


def executar_consulta(spec: dict, *, serpapi_key: Optional[str] = None, serpapi_teto_atingido: bool = False) -> dict:
    """Roda uma consulta com retry+backoff no fast-flights; se esgotar,
    tenta SerpApi só nessa rodada (se houver chave e o teto de chamadas
    não estiver atingido). Nunca levanta exceção
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

    if serpapi_key and serpapi_teto_atingido:
        return {
            "ok": False,
            "fonte": "nenhuma",
            "ofertas": [],
            "google_price_level": None,
            "erro": f"fast-flights falhou ({erro_fast_flights}); fallback SerpApi bloqueado: teto de {config.MAX_SERPAPI_POR_24H} chamadas/24h atingido",
        }

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

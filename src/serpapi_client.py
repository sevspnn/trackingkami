"""Cliente do SerpApi (engine google_flights) — usado como âncora histórica
(bootstrap + recalibração semanal) e, mais adiante, como fallback pontual
quando o fast-flights leva captcha/bloqueio.

Achados testando a API real (ver ETAPA 5):
- destino precisa ser AEROPORTO de verdade (ex: 'CNF'), não código de
  cidade ('BHZ' não retorna nada aqui — diferente do fast-flights, que
  aceita city code de boa). 'PLU' sozinho também não retornou resultado
  pra essa rota (aeroporto secundário, baixo volume).
- price_insights.price_history é uma lista de pares [timestamp_unix_utc,
  preco_inteiro_em_reais] com granularidade diária, cobrindo os últimos
  ~60 dias a partir de HOJE — não é atrelado às datas de ida/volta
  buscadas, é a tendência geral da rota.
- lowest_price / typical_price_range vêm em reais inteiros (sem centavos).
- Se a busca não achar nada, a resposta vem com status 200 e uma chave
  'error' (não HTTP 4xx/5xx) — precisa checar 'error' explicitamente.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Literal, Optional

import requests

SERPAPI_URL = "https://serpapi.com/search.json"
AEROPORTO_DESTINO_ANCORA = "CNF"  # BHZ (city code) não funciona nesta API; CNF é o principal


class SerpApiError(Exception):
    pass


@dataclass
class PriceInsights:
    lowest_price_centavos: Optional[int]
    price_level: Optional[str]
    typical_low_centavos: Optional[int]
    typical_high_centavos: Optional[int]
    price_history: list[tuple[datetime, int]]  # (ts_utc, preco_centavos)


def _reais_para_centavos(v: Optional[float]) -> Optional[int]:
    return round(v * 100) if v is not None else None


def buscar_raw(
    *,
    api_key: str,
    origem: str,
    data_ida: str,
    data_volta: Optional[str] = None,
    destino: str = AEROPORTO_DESTINO_ANCORA,
    currency: str = "BRL",
    timeout: int = 30,
) -> dict:
    """Chamada de baixo nível — devolve o JSON cru. Levanta SerpApiError em
    qualquer falha (rede, chave inválida, sem resultado)."""
    params = {
        "engine": "google_flights",
        "departure_id": origem,
        "arrival_id": destino,
        "outbound_date": data_ida,
        "currency": currency,
        "hl": "en",
        "gl": "br",
        "adults": 1,
        "type": 1 if data_volta else 2,
        "api_key": api_key,
    }
    if data_volta:
        params["return_date"] = data_volta

    try:
        r = requests.get(SERPAPI_URL, params=params, timeout=timeout)
    except requests.RequestException as e:
        raise SerpApiError(f"falha de rede: {e}") from e

    try:
        data = r.json()
    except ValueError as e:
        raise SerpApiError(f"resposta não é JSON (status {r.status_code}): {e}") from e

    if "error" in data:
        raise SerpApiError(f"SerpApi retornou erro: {data['error']}")

    return data


def buscar_price_insights(
    *,
    api_key: str,
    origem: str,
    data_ida: str,
    data_volta: Optional[str] = None,
    destino: str = AEROPORTO_DESTINO_ANCORA,
    currency: str = "BRL",
    timeout: int = 30,
) -> PriceInsights:
    data = buscar_raw(
        api_key=api_key,
        origem=origem,
        data_ida=data_ida,
        data_volta=data_volta,
        destino=destino,
        currency=currency,
        timeout=timeout,
    )

    pi = data.get("price_insights")
    if not pi:
        raise SerpApiError("resposta sem bloco price_insights")

    typical = pi.get("typical_price_range") or [None, None]
    historico = [
        (datetime.fromtimestamp(ts, tz=timezone.utc), _reais_para_centavos(preco))
        for ts, preco in pi.get("price_history", [])
    ]

    return PriceInsights(
        lowest_price_centavos=_reais_para_centavos(pi.get("lowest_price")),
        price_level=pi.get("price_level"),
        typical_low_centavos=_reais_para_centavos(typical[0]),
        typical_high_centavos=_reais_para_centavos(typical[1]),
        price_history=historico,
    )

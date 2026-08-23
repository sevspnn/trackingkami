"""Converte um `fast_flights.Result` (schema achatado da lib 2.2) em linhas
prontas para `db.insert_consulta` / `db.insert_oferta`.

Notas sobre a API real da lib (ver ETAPA 1):
- Result.flights é uma lista achatada de Flight, sem segmentos de conexão.
- Numa consulta round-trip, só a perna de IDA vem com horário/duração —
  o preço é o total ida+volta. É por isso que o projeto faz consultas
  one-way separadas para pegar os detalhes reais da volta.
- price vem como string "R$1762" (símbolo de moeda + dígitos, sem decimais).
- duration vem como string "6 hr 5 min" (ou só "45 min", ou só "6 hr").
- departure/arrival vêm como string livre "1:00 PM on Thu, Oct 8".

Fuso: NAT (America/Fortaleza) e BHZ/CNF/PLU (America/Sao_Paulo) são os dois
UTC-3 fixo, sem horário de verão desde 2019. Por isso é seguro tratar
qualquer horário local retornado pela lib (origem ou destino) como UTC-3
fixo ao converter para UTC. Isso NÃO valeria se algum dia aparecesse uma
conexão em outro fuso — não é o caso desta rota.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional

BRT = timezone(timedelta(hours=-3))

_PRICE_RE = re.compile(r"R\$\s*([\d.,]+)")
_DURATION_RE = re.compile(r"(?:(\d+)\s*hr)?\s*(?:(\d+)\s*min)?")


class ParseError(ValueError):
    """Erro de parsing considerado grave o bastante para marcar a consulta como falha."""


def parse_price_to_centavos(price_str: str) -> int:
    """'R$1762' -> 176200. Levanta ParseError se não achar o símbolo R$
    (sinal de que a moeda vazou pra outra, ex. USD por causa do IP)."""
    if not price_str or not price_str.strip():
        raise ParseError(f"preço vazio: {price_str!r}")
    m = _PRICE_RE.search(price_str)
    if not m:
        raise ParseError(f"preço sem símbolo R$ (moeda pode ter vazado): {price_str!r}")
    digits = m.group(1).replace(".", "").replace(",", "")
    if not digits.isdigit():
        raise ParseError(f"preço não numérico: {price_str!r}")
    return int(digits) * 100


def parse_duration_to_min(duration_str: str) -> Optional[int]:
    """'6 hr 5 min' -> 365. '45 min' -> 45. '' -> None."""
    if not duration_str or not duration_str.strip():
        return None
    m = _DURATION_RE.search(duration_str)
    if not m or (m.group(1) is None and m.group(2) is None):
        return None
    hours = int(m.group(1)) if m.group(1) else 0
    minutes = int(m.group(2)) if m.group(2) else 0
    return hours * 60 + minutes


def _parse_dt(text: str, reference_year: int) -> Optional[str]:
    """'1:00 PM on Thu, Oct 8' -> ISO 8601 UTC. None se não der pra parsear."""
    if not text or " on " not in text:
        return None
    time_part, date_part = text.split(" on ", 1)
    try:
        t = datetime.strptime(time_part.strip(), "%I:%M %p")
        d = datetime.strptime(f"{date_part.strip()} {reference_year}", "%a, %b %d %Y")
    except ValueError:
        return None
    local_dt = datetime(
        d.year, d.month, d.day, t.hour, t.minute, tzinfo=BRT
    )
    return local_dt.astimezone(timezone.utc).isoformat()


@dataclass
class ParsedOferta:
    cia: str
    preco_centavos: int
    moeda: str
    is_best: bool
    aeroporto_origem: Optional[str]
    aeroporto_destino: Optional[str]
    saida_ts: Optional[str]
    chegada_ts: Optional[str]
    duracao_min: Optional[int]
    n_paradas: Optional[int]
    conexoes_json: Optional[str]
    aeronave: Optional[str]

    def as_row(self) -> dict:
        return {
            "cia": self.cia,
            "preco_centavos": self.preco_centavos,
            "moeda": self.moeda,
            "is_best": int(self.is_best),
            "aeroporto_origem": self.aeroporto_origem,
            "aeroporto_destino": self.aeroporto_destino,
            "saida_ts": self.saida_ts,
            "chegada_ts": self.chegada_ts,
            "duracao_min": self.duracao_min,
            "n_paradas": self.n_paradas,
            "conexoes_json": self.conexoes_json,
            "aeronave": self.aeronave,
        }


def parse_flight(
    flight,
    *,
    reference_date: str,
    aeroporto_origem: str,
    aeroporto_destino: str,
    moeda: str = "BRL",
) -> ParsedOferta:
    """flight: instância de fast_flights.schema.Flight
    reference_date: 'YYYY-MM-DD' do trecho efetivamente buscado nessa
        consulta (para dar o ano ao parsear 'Thu, Oct 8'). Numa consulta
        round-trip isso é a data de ida, porque só a ida vem detalhada.
    """
    ref_year = int(reference_date[:4])
    preco_centavos = parse_price_to_centavos(flight.price)
    duracao_min = parse_duration_to_min(flight.duration)
    saida_ts = _parse_dt(flight.departure, ref_year)
    chegada_ts = _parse_dt(flight.arrival, ref_year)

    # Guarda contra virada de ano só por segurança (não deve ocorrer nas
    # datas deste projeto, mas é barato proteger).
    if saida_ts and chegada_ts and chegada_ts < saida_ts:
        chegada_ts = _parse_dt(flight.arrival, ref_year + 1)

    n_paradas = flight.stops if isinstance(flight.stops, int) else None

    return ParsedOferta(
        cia=flight.name or None,
        preco_centavos=preco_centavos,
        moeda=moeda,
        is_best=bool(flight.is_best),
        aeroporto_origem=aeroporto_origem,
        aeroporto_destino=aeroporto_destino,
        saida_ts=saida_ts,
        chegada_ts=chegada_ts,
        duracao_min=duracao_min,
        n_paradas=n_paradas,
        conexoes_json=None,  # lib não expõe aeroportos de conexão nesta versão
        aeronave=None,  # lib não expõe aeronave nesta versão
    )


def parse_result(
    result,
    *,
    reference_date: str,
    aeroporto_origem: str,
    aeroporto_destino: str,
    moeda: str = "BRL",
) -> list[ParsedOferta]:
    """Levanta ParseError se result.flights estiver vazio — zero ofertas é
    falha, não resultado vazio (ver seção Robustez do projeto)."""
    if not result.flights:
        raise ParseError("zero ofertas retornadas — tratar como falha da rodada")

    ofertas = []
    erros = []
    for f in result.flights:
        try:
            ofertas.append(
                parse_flight(
                    f,
                    reference_date=reference_date,
                    aeroporto_origem=aeroporto_origem,
                    aeroporto_destino=aeroporto_destino,
                    moeda=moeda,
                )
            )
        except ParseError as e:
            erros.append(str(e))

    if not ofertas:
        raise ParseError(
            f"todas as {len(result.flights)} ofertas falharam no parsing: {erros[:3]}"
        )

    return ofertas

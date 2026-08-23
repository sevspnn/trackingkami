"""Métricas de preço. Tudo calculado na hora a partir de uma lista de pontos
(ts_utc, preco_centavos) vinda do banco — nada fica armazenado/cacheado.

Duas séries paralelas são alimentadas por quem chama este módulo (ver README):
- mais_barato_da_rodada: a oferta mais barata de cada rodada, pode trocar de
  itinerário/cia entre rodadas. Serve pra achar oportunidade.
- itinerario_referencia: preço de uma cia + faixa de horário fixas entre
  rodadas. Serve pra medir variação de preço de verdade, sem confundir com
  troca de produto.

Este módulo não sabe qual série está recebendo — ele só faz conta em cima de
list[PricePoint]. Mediana é preferida a média em qualquer referência de alerta.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional

MIN_AMOSTRAS_PARA_PORCENTAGEM = 8
LIMIAR_QUEDA_SIGNIFICATIVA_PCT = 8.0
LIMIAR_VARIACAO_SUSPEITA_PCT = 60.0


@dataclass(frozen=True)
class PricePoint:
    ts_utc: datetime
    preco_centavos: int


def mediana_movel_14d(pontos: list[PricePoint], as_of: datetime, dias: int = 14) -> Optional[int]:
    """Mediana dos preços com ts_utc em [as_of - dias, as_of]. None se a janela estiver vazia."""
    limite_inferior = as_of - timedelta(days=dias)
    janela = [p.preco_centavos for p in pontos if limite_inferior <= p.ts_utc <= as_of]
    if not janela:
        return None
    return round(statistics.median(janela))


def minimo_historico(pontos: list[PricePoint]) -> Optional[PricePoint]:
    if not pontos:
        return None
    return min(pontos, key=lambda p: p.preco_centavos)


def media_geral(pontos: list[PricePoint]) -> tuple[Optional[float], int]:
    """Retorna (média_em_centavos, n). Sempre exibir os dois juntos."""
    n = len(pontos)
    if n == 0:
        return None, 0
    return statistics.mean(p.preco_centavos for p in pontos), n


def variacao_pct(preco_atual_centavos: int, referencia_centavos: Optional[int]) -> Optional[float]:
    if not referencia_centavos:
        return None
    return (preco_atual_centavos - referencia_centavos) / referencia_centavos * 100.0


def formatar_comparacao_mediana(
    preco_atual_centavos: int,
    pontos_historicos: list[PricePoint],
    as_of: datetime,
    min_amostras: int = MIN_AMOSTRAS_PARA_PORCENTAGEM,
) -> str:
    """'-12% mediana' ou 'coletando histórico (n=3)' se ainda não há amostra
    suficiente. n conta o total de pontos históricos disponíveis (não só os
    da janela de 14d), porque é isso que o usuário quer saber: 'quantas
    rodadas eu já tenho'."""
    n = len(pontos_historicos)
    if n < min_amostras:
        return f"coletando histórico (n={n})"
    mediana = mediana_movel_14d(pontos_historicos, as_of)
    if mediana is None:
        return f"coletando histórico (n={n})"
    pct = variacao_pct(preco_atual_centavos, mediana)
    sinal = "+" if pct >= 0 else ""
    return f"{sinal}{pct:.0f}% mediana"


def eh_novo_minimo(preco_atual_centavos: int, pontos_historicos: list[PricePoint]) -> bool:
    minimo = minimo_historico(pontos_historicos)
    if minimo is None:
        return False
    return preco_atual_centavos < minimo.preco_centavos


def eh_queda_significativa(
    preco_atual_centavos: int,
    pontos_historicos: list[PricePoint],
    as_of: datetime,
    limiar_pct: float = LIMIAR_QUEDA_SIGNIFICATIVA_PCT,
) -> bool:
    """True se preco_atual estiver mais de limiar_pct% ABAIXO da mediana móvel de 14d
    (limite exclusivo: exatamente -8% não dispara, precisa passar de -8%)."""
    mediana = mediana_movel_14d(pontos_historicos, as_of)
    if mediana is None:
        return False
    pct = variacao_pct(preco_atual_centavos, mediana)
    return pct is not None and pct < -limiar_pct


def eh_variacao_suspeita(
    preco_atual_centavos: int,
    preco_rodada_anterior_centavos: Optional[int],
    limiar_pct: float = LIMIAR_VARIACAO_SUSPEITA_PCT,
) -> bool:
    """True se a variação entre rodadas consecutivas (mesmo itinerário) passar
    de limiar_pct% pra cima ou pra baixo. Não impede a gravação, só sinaliza."""
    if not preco_rodada_anterior_centavos:
        return False
    pct = variacao_pct(preco_atual_centavos, preco_rodada_anterior_centavos)
    return pct is not None and abs(pct) > limiar_pct


@dataclass(frozen=True)
class ComparacaoOneWayRoundTrip:
    soma_oneways_centavos: int
    roundtrip_centavos: int
    diff_centavos: int  # positivo = round-trip mais barato que a soma dos two one-ways
    roundtrip_mais_barato: bool

    def resumo(self) -> str:
        reais = abs(self.diff_centavos) / 100
        if self.roundtrip_mais_barato:
            return f"round-trip R$ {reais:,.2f} mais barato que 2x one-way"
        return f"2x one-way R$ {reais:,.2f} mais barato que round-trip"


def comparar_oneways_vs_roundtrip(
    preco_ida_centavos: int, preco_volta_centavos: int, preco_roundtrip_centavos: int
) -> ComparacaoOneWayRoundTrip:
    soma = preco_ida_centavos + preco_volta_centavos
    diff = soma - preco_roundtrip_centavos
    return ComparacaoOneWayRoundTrip(
        soma_oneways_centavos=soma,
        roundtrip_centavos=preco_roundtrip_centavos,
        diff_centavos=diff,
        roundtrip_mais_barato=diff > 0,
    )

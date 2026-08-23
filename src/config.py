"""Configuração da viagem e dos parâmetros do rastreador. Único lugar que
deveria mudar se a viagem/regras de alerta mudarem."""

from __future__ import annotations

ORIGEM = "NAT"
DESTINO = "BHZ"  # city code — fast-flights aceita direto; SerpApi usa CNF (ver serpapi_client.AEROPORTO_DESTINO_ANCORA)

DATA_IDA = "2026-10-08"
DATA_VOLTA = "2026-10-12"
DATA_VOLTA_FLEXIVEL = "2026-10-13"
VARIANTE_FLEXIVEL_ATIVA = True

MOEDA = "BRL"
PASSAGEIROS_ADULTOS = 1
CLASSE = "economy"

# Rodada em que o e-mail de rotina sai (hora local America/Fortaleza).
EMAIL_ROTINA_HORA_LOCAL = 7

MAX_TENTATIVAS_FAST_FLIGHTS = 2
BACKOFF_BASE_SEGUNDOS = 5

JITTER_ENTRE_CONSULTAS_MIN_S = 3
JITTER_ENTRE_CONSULTAS_MAX_S = 8

ANTI_SPAM_HORAS = 4

# Itinerário de referência (perna de ida): fixa uma cia + janela de horário
# de saída pra medir variação de preço sem confundir com troca de produto
# entre rodadas. None enquanto não decidido — nesse estado, a série
# itinerario_referencia fica vazia e só mais_barato_da_rodada é usada.
#
# Fixado em 23/08/2026 a partir dos candidatos da primeira rodada real:
# LATAM, saída 16:00 (6h de duração, 1 parada, R$ 1.762,00 na rodada em que
# foi escolhido). Janela de 30min pra tolerar pequenas mudanças de horário
# da própria cia entre rodadas sem perder o ponto.
ITINERARIO_REFERENCIA = {"cia": "LATAM", "saida_ida_hora_min": "15:30", "saida_ida_hora_max": "16:30"}

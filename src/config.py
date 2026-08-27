"""Configuração das viagens rastreadas e dos parâmetros do rastreador.

Multi-rota desde 27/08/2026: cada rota tem origem/destino/datas e e-mail
de destino próprios, e roda de forma independente (histórico, alertas e
anti-spam não se misturam entre rotas — ver coluna `rota` em db.py).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass
class RotaConfig:
    id: str  # slug estável, usado como chave no banco — não mude depois de criar histórico
    origem: str
    destino: str
    data_ida: str
    data_volta: str
    email_env_var: str  # nome da env var (secret do GitHub Actions ou .env local) com o e-mail de destino
    data_volta_flexivel: Optional[str] = None
    variante_flexivel_ativa: bool = False
    # Itinerário de referência (perna de ida): cia + janela de horário de
    # saída fixadas depois de ver os candidatos da primeira rodada real
    # (ver ETAPA "primeira execução" no histórico do projeto). None até
    # decidir — nesse estado só a série mais_barato_da_rodada é usada.
    itinerario_referencia: Optional[dict] = None


MOEDA = "BRL"
PASSAGEIROS_ADULTOS = 1
CLASSE = "economy"

# Rodada em que o e-mail de rotina sai (hora local America/Fortaleza), igual pra todas as rotas.
EMAIL_ROTINA_HORA_LOCAL = 7

MAX_TENTATIVAS_FAST_FLIGHTS = 2
BACKOFF_BASE_SEGUNDOS = 5

JITTER_ENTRE_CONSULTAS_MIN_S = 3
JITTER_ENTRE_CONSULTAS_MAX_S = 8

ANTI_SPAM_HORAS = 4

ROTAS: list[RotaConfig] = [
    RotaConfig(
        id="maceio",
        origem="NAT",
        destino="MCZ",
        data_ida="2026-12-19",
        data_volta="2026-12-21",
        email_env_var="EMAIL_DESTINO",
        # Fixado em 27/08/2026 a partir dos candidatos da primeira rodada real.
        itinerario_referencia={"cia": "Gol", "saida_ida_hora_min": "11:40", "saida_ida_hora_max": "12:40"},
    ),
    RotaConfig(
        id="sao_luis",
        origem="NAT",
        destino="SLZ",
        data_ida="2027-01-08",
        data_volta="2027-01-11",
        email_env_var="EMAIL_DESTINO_2",
        # Fixado em 27/08/2026 a partir dos candidatos da primeira rodada real.
        itinerario_referencia={"cia": "LATAM", "saida_ida_hora_min": "11:55", "saida_ida_hora_max": "12:55"},
    ),
]

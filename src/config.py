"""Configuração das viagens rastreadas e dos parâmetros do rastreador.

Multi-rota desde 27/08/2026: cada rota tem origem/destino/datas e e-mail
de destino próprios, e roda de forma independente (histórico, alertas e
anti-spam não se misturam entre rotas — ver coluna `rota` em db.py).
"""

from __future__ import annotations

from dataclasses import dataclass, field
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
    # Modo "dois trechos" (adicionado em 05/11/2026): volta saindo/chegando
    # em lugar diferente da ida (ex: NAT→BSB, depois BSB→REC). Preenchendo
    # volta_origem/volta_destino, a rota deixa de consultar round-trip e
    # passa a rastrear só trechos avulsos (one-way), um por data — o preço
    # da viagem é ida mais barata + volta mais barata. Voo doméstico no
    # Brasil é precificado por trecho, então isso é o que você pagaria
    # comprando as duas passagens separadas.
    volta_origem: Optional[str] = None
    volta_destino: Optional[str] = None
    # Datas alternativas além de data_ida/data_volta (só no modo dois
    # trechos): cada uma vira uma consulta one-way a mais por rodada.
    datas_ida_extras: list[str] = field(default_factory=list)
    datas_volta_extras: list[str] = field(default_factory=list)

    @property
    def dois_trechos(self) -> bool:
        return bool(self.volta_origem and self.volta_destino)

    @property
    def datas_ida(self) -> list[str]:
        return [self.data_ida, *self.datas_ida_extras]

    @property
    def datas_volta(self) -> list[str]:
        return [self.data_volta, *self.datas_volta_extras]

    @property
    def rotulo(self) -> str:
        """'NAT>MCZ' ou, no modo dois trechos, 'NAT>BSB>REC' (ou
        'NAT>BSB / GRU>REC' se a volta não sair de onde a ida chegou)."""
        if not self.dois_trechos:
            return f"{self.origem}>{self.destino}"
        if self.volta_origem == self.destino:
            return f"{self.origem}>{self.destino}>{self.volta_destino}"
        return f"{self.origem}>{self.destino} / {self.volta_origem}>{self.volta_destino}"


MOEDA = "BRL"
PASSAGEIROS_ADULTOS = 1
CLASSE = "economy"

# Rodada em que o e-mail de rotina sai (hora local America/Fortaleza), igual pra todas as rotas.
EMAIL_ROTINA_HORA_LOCAL = 7

MAX_TENTATIVAS_FAST_FLIGHTS = 2
BACKOFF_BASE_SEGUNDOS = 5

# Teto de chamadas ao SerpApi (fallback) numa janela móvel de 24h, somando todas as
# rotas. O plano tem 250 buscas/mês; em 13/09/2026 um bug no fast-flights mandou
# todas as consultas pro fallback (~36/dia) e drenou 90% da quota. Com 6/dia o
# pior caso do mês fica em 180. Estourou o teto = a consulta falha em vez de gastar.
MAX_SERPAPI_POR_24H = 6

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
    # Desativada em 01/11/2026. O histórico continua no banco sob o id "sao_luis";
    # pra reativar, é só descomentar.
    # RotaConfig(
    #     id="sao_luis",
    #     origem="NAT",
    #     destino="SLZ",
    #     data_ida="2027-01-08",
    #     data_volta="2027-01-11",
    #     email_env_var="EMAIL_DESTINO_2",
    #     # Fixado em 27/08/2026 a partir dos candidatos da primeira rodada real.
    #     itinerario_referencia={"cia": "LATAM", "saida_ida_hora_min": "11:55", "saida_ida_hora_max": "12:55"},
    # ),
    RotaConfig(
        # id novo (era "belo_horizonte", 29/12 → 05/01) porque as datas mudaram em
        # 19/10/2026: o histórico é por rota, não por data, e misturar preços de
        # datas diferentes estragaria mediana/mínimo. O histórico antigo continua
        # no banco sob o id "belo_horizonte".
        id="belo_horizonte_natal",
        origem="BHZ",  # city code — fast-flights aceita direto; SerpApi traduz pra CNF (ver fast_flights_client._TRADUCAO_SERPAPI)
        destino="NAT",
        data_ida="2026-12-25",
        data_volta="2027-01-01",
        email_env_var="EMAIL_DESTINO_3",
        # Fixado em 05/09/2026 a partir dos candidatos da primeira rodada real
        # (pras datas antigas — revisar se a LATAM nessa janela não existir em 25/12).
        itinerario_referencia={"cia": "LATAM", "saida_ida_hora_min": "10:00", "saida_ida_hora_max": "11:00"},
    ),
    RotaConfig(
        # Ida e volta por cidades diferentes (adicionada em 05/11/2026):
        # NAT→BSB em 02 ou 03/01, BSB→REC em 07 ou 08/01. Rastreia as 4
        # combinações como trechos avulsos (ver modo "dois trechos" acima).
        id="natal_brasilia_recife",
        origem="NAT",
        destino="BSB",
        data_ida="2027-01-02",
        datas_ida_extras=["2027-01-03"],
        volta_origem="BSB",
        volta_destino="REC",
        data_volta="2027-01-07",
        datas_volta_extras=["2027-01-08"],
        email_env_var="EMAIL_DESTINO_4",
    ),
]

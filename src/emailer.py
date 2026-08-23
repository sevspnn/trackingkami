"""Renderização e envio do e-mail de alerta.

Duas responsabilidades separadas de propósito:
- render_*(): pura, sem I/O, retorna (assunto, html). Fácil de testar.
- send_email(): só faz SMTP.
"""

from __future__ import annotations

import smtplib
from dataclasses import dataclass, field
from datetime import datetime
from email.message import EmailMessage
from typing import Literal, Optional
from zoneinfo import ZoneInfo

FUSO_EXIBICAO = ZoneInfo("America/Fortaleza")


def formatar_reais(centavos: int, casas: int = 2) -> str:
    """342000 -> 'R$ 3.420,00' (casas=2) ou 'R$ 3.420' (casas=0). Formatação
    brasileira manual (ponto milhar, vírgula decimal) pra não depender de
    locale do sistema, que é inconsistente entre a máquina local e o runner
    do GitHub Actions."""
    sinal = "-" if centavos < 0 else ""
    c = abs(centavos)
    if casas == 0:
        reais = round(c / 100)
        parte_decimal = ""
    else:
        reais = c // 100
        parte_decimal = f",{c % 100:02d}"
    s = str(reais)
    grupos = []
    while len(s) > 3:
        grupos.insert(0, s[-3:])
        s = s[:-3]
    grupos.insert(0, s)
    return f"R$ {sinal}{'.'.join(grupos)}{parte_decimal}"


def formatar_pct(pct: Optional[float]) -> str:
    if pct is None:
        return "?"
    sinal = "+" if pct >= 0 else ""
    return f"{sinal}{pct:.0f}%"


def formatar_data_curta(data_iso: str) -> str:
    """'2026-10-08' -> '08/10'"""
    y, m, d = data_iso.split("-")
    return f"{d}/{m}"


def formatar_hora_local(ts: Optional[datetime]) -> str:
    if ts is None:
        return "?"
    return ts.astimezone(FUSO_EXIBICAO).strftime("%H:%M")


def formatar_duracao(min_: Optional[int]) -> str:
    if min_ is None:
        return "?"
    h, m = divmod(min_, 60)
    return f"{h}h{m:02d}"


@dataclass
class PernaVoo:
    cia: Optional[str]
    saida_ts: Optional[datetime]
    chegada_ts: Optional[datetime]
    duracao_min: Optional[int]
    n_paradas: Optional[int]


@dataclass
class ComparacaoOW:
    soma_oneways_centavos: int
    roundtrip_centavos: int
    roundtrip_mais_barato: bool
    diff_centavos: int


@dataclass
class VarianteFlexivel:
    data_volta: str  # 'YYYY-MM-DD'
    preco_centavos: int
    pct_mediana: Optional[float]
    n_amostras: int
    perna_volta: Optional[PernaVoo] = None


@dataclass
class RodadaContext:
    origem: str
    destino: str
    data_ida: str
    data_volta: str
    preco_centavos: int
    n_amostras: int
    pct_mediana: Optional[float] = None  # None = ainda coletando histórico
    mediana_centavos: Optional[int] = None
    minimo_historico_centavos: Optional[int] = None
    media_centavos: Optional[float] = None
    google_price_level: Optional[str] = None
    perna_ida: Optional[PernaVoo] = None
    perna_volta: Optional[PernaVoo] = None
    comparacao_ow: Optional[ComparacaoOW] = None
    variante_flexivel: Optional[VarianteFlexivel] = None


@dataclass
class FalhaContext:
    origem: str
    destino: str
    rodada_hora_local: str  # 'HH:MM', já em horário local
    erro: str
    ts_utc: Optional[datetime] = None


_CSS = """
body { font-family: -apple-system, Arial, sans-serif; color: #1a1a1a; }
table { border-collapse: collapse; width: 100%; max-width: 640px; }
td, th { padding: 6px 10px; border: 1px solid #ddd; text-align: left; font-size: 14px; }
th { background: #f4f4f4; }
.preco { font-size: 22px; font-weight: bold; }
.queda { color: #0a7d2c; }
.alta { color: #b3261e; }
.banner-alerta { background: #fff4e5; border: 1px solid #f0b429; padding: 10px; margin-bottom: 12px; }
.banner-falha { background: #fdecea; border: 1px solid #e53935; padding: 10px; margin-bottom: 12px; }
.muted { color: #666; font-size: 12px; }
"""


def _linha_perna(rotulo: str, perna: Optional[PernaVoo]) -> str:
    if perna is None:
        return f"<tr><th>{rotulo}</th><td colspan='2'>sem dados</td></tr>"
    paradas = "direto" if perna.n_paradas == 0 else f"{perna.n_paradas} parada(s)"
    return (
        f"<tr><th>{rotulo}</th>"
        f"<td>{perna.cia or '?'} · {formatar_hora_local(perna.saida_ts)} → "
        f"{formatar_hora_local(perna.chegada_ts)} · {formatar_duracao(perna.duracao_min)}</td>"
        f"<td>{paradas}</td></tr>"
    )


def _linha_comparacoes(ctx: RodadaContext) -> str:
    mediana_txt = formatar_reais(ctx.mediana_centavos, 2) if ctx.mediana_centavos else "-"
    minimo_txt = formatar_reais(ctx.minimo_historico_centavos, 2) if ctx.minimo_historico_centavos else "-"
    media_txt = (
        f"{formatar_reais(round(ctx.media_centavos), 2)} (n={ctx.n_amostras})"
        if ctx.media_centavos
        else f"coletando histórico (n={ctx.n_amostras})"
    )
    pct_txt = (
        f"{formatar_pct(ctx.pct_mediana)} vs mediana 14d"
        if ctx.pct_mediana is not None
        else f"coletando histórico (n={ctx.n_amostras})"
    )
    nivel_txt = ctx.google_price_level or "-"
    return f"""
    <tr><th>Mediana 14d</th><td colspan="2">{mediana_txt} ({pct_txt})</td></tr>
    <tr><th>Mínimo histórico</th><td colspan="2">{minimo_txt}</td></tr>
    <tr><th>Média geral</th><td colspan="2">{media_txt}</td></tr>
    <tr><th>Nível Google</th><td colspan="2">{nivel_txt}</td></tr>
    """


def _linha_ow_comparacao(comp: Optional[ComparacaoOW]) -> str:
    if comp is None:
        return ""
    if comp.roundtrip_mais_barato:
        txt = f"round-trip {formatar_reais(comp.roundtrip_centavos)} é {formatar_reais(comp.diff_centavos)} mais barato que 2x one-way ({formatar_reais(comp.soma_oneways_centavos)})"
    else:
        txt = f"2x one-way ({formatar_reais(comp.soma_oneways_centavos)}) é {formatar_reais(-comp.diff_centavos)} mais barato que round-trip ({formatar_reais(comp.roundtrip_centavos)})"
    return f'<tr><th>2x one-way vs round-trip</th><td colspan="2">{txt}</td></tr>'


def _linha_variante(v: Optional[VarianteFlexivel]) -> str:
    if v is None:
        return ""
    pct_txt = formatar_pct(v.pct_mediana) if v.pct_mediana is not None else f"coletando histórico (n={v.n_amostras})"
    perna_txt = ""
    if v.perna_volta:
        perna_txt = f" · volta {formatar_hora_local(v.perna_volta.saida_ts)}→{formatar_hora_local(v.perna_volta.chegada_ts)}"
    return (
        f'<tr><th>Variante volta {formatar_data_curta(v.data_volta)}</th>'
        f"<td colspan='2'>{formatar_reais(v.preco_centavos)} ({pct_txt}){perna_txt}</td></tr>"
    )


def _corpo_tabela(ctx: RodadaContext) -> str:
    return f"""
    <table>
      <tr><th colspan="3" class="preco">{formatar_reais(ctx.preco_centavos)}</th></tr>
      {_linha_perna("Ida " + formatar_data_curta(ctx.data_ida), ctx.perna_ida)}
      {_linha_perna("Volta " + formatar_data_curta(ctx.data_volta), ctx.perna_volta)}
      {_linha_comparacoes(ctx)}
      {_linha_ow_comparacao(ctx.comparacao_ow)}
      {_linha_variante(ctx.variante_flexivel)}
    </table>
    <p class="muted">{ctx.origem}&gt;{ctx.destino} · gerado {datetime.now(FUSO_EXIBICAO).strftime('%d/%m %H:%M')} (America/Fortaleza)</p>
    """


def render_rotina(ctx: RodadaContext) -> tuple[str, str]:
    if ctx.pct_mediana is None:
        meio_assunto = f"coletando histórico (n={ctx.n_amostras})"
    else:
        meio_assunto = f"{formatar_pct(ctx.pct_mediana)} mediana | n={ctx.n_amostras}"
    assunto = (
        f"{ctx.origem}>{ctx.destino} {formatar_data_curta(ctx.data_ida)}-{formatar_data_curta(ctx.data_volta)} "
        f"| {formatar_reais(ctx.preco_centavos, 0)} | {meio_assunto}"
    )
    html = f"<html><head><style>{_CSS}</style></head><body>{_corpo_tabela(ctx)}</body></html>"
    return assunto, html


def render_alerta(ctx: RodadaContext, motivo: Literal["novo_minimo", "queda_significativa"]) -> tuple[str, str]:
    if motivo == "novo_minimo":
        rotulo = "NOVO MINIMO"
    else:
        rotulo = "QUEDA"
    pct_txt = f" ({formatar_pct(ctx.pct_mediana)})" if ctx.pct_mediana is not None else ""
    assunto = f"{ctx.origem}>{ctx.destino} | {rotulo} {formatar_reais(ctx.preco_centavos, 0)}{pct_txt}"
    banner = f'<div class="banner-alerta"><b>{rotulo}</b>: {formatar_reais(ctx.preco_centavos)}{pct_txt}</div>'
    html = f"<html><head><style>{_CSS}</style></head><body>{banner}{_corpo_tabela(ctx)}</body></html>"
    return assunto, html


def render_falha(ctx: FalhaContext) -> tuple[str, str]:
    assunto = f"{ctx.origem}>{ctx.destino} | FALHA na coleta das {ctx.rodada_hora_local}"
    banner = f'<div class="banner-falha"><b>Falha na coleta</b> — rodada das {ctx.rodada_hora_local}</div>'
    erro_esc = ctx.erro.replace("<", "&lt;").replace(">", "&gt;")
    html = (
        f"<html><head><style>{_CSS}</style></head><body>{banner}"
        f"<pre style='white-space:pre-wrap;font-size:12px;background:#f4f4f4;padding:8px'>{erro_esc}</pre>"
        f"</body></html>"
    )
    return assunto, html


def send_email(
    *,
    gmail_user: str,
    gmail_app_password: str,
    destino: str,
    assunto: str,
    html: str,
) -> None:
    msg = EmailMessage()
    msg["Subject"] = assunto
    msg["From"] = gmail_user
    msg["To"] = destino
    msg.set_content("Este e-mail requer um cliente compatível com HTML.")
    msg.add_alternative(html, subtype="html")

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as smtp:
        smtp.login(gmail_user, gmail_app_password)
        smtp.send_message(msg)

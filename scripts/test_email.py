"""Teste manual da etapa 4: renderiza os 3 tipos de e-mail com dados
sintéticos, salva preview em HTML, e opcionalmente envia de verdade.

Uso:
    python scripts/test_email.py                # só gera preview_*.html
    python scripts/test_email.py --send          # também envia via Gmail
                                                  # (lê credenciais de .env)

O .env (não versionado) deve ter:
    GMAIL_USER=voce@gmail.com
    GMAIL_APP_PASSWORD=xxxx xxxx xxxx xxxx
    EMAIL_DESTINO=voce@gmail.com
"""

import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from emailer import (
    ComparacaoOW,
    FalhaContext,
    PernaVoo,
    RodadaContext,
    VarianteFlexivel,
    render_alerta,
    render_falha,
    render_rotina,
    send_email,
)

ROOT = Path(__file__).resolve().parent.parent


def carregar_env(path: Path) -> dict:
    env = {}
    if not path.exists():
        return env
    for linha in path.read_text().splitlines():
        linha = linha.strip()
        if not linha or linha.startswith("#") or "=" not in linha:
            continue
        chave, _, valor = linha.partition("=")
        env[chave.strip()] = valor.strip()
    return env


def dt(hora_min: str, dia: int) -> datetime:
    h, m = map(int, hora_min.split(":"))
    return datetime(2026, 10, dia, h, m, tzinfo=timezone.utc)


def ctx_rotina() -> RodadaContext:
    """Reproduz o exemplo do prompt: 'NAT>BHZ 08/10-12/10 | R$ 3.420 | -12% mediana | n=41'"""
    return RodadaContext(
        origem="NAT",
        destino="BHZ",
        data_ida="2026-10-08",
        data_volta="2026-10-12",
        preco_centavos=342000,
        n_amostras=41,
        pct_mediana=-12.0,
        mediana_centavos=388000,
        minimo_historico_centavos=298000,
        media_centavos=401500,
        google_price_level="typical",
        perna_ida=PernaVoo(
            cia="LATAM", saida_ts=dt("16:00", 8), chegada_ts=dt("22:05", 8), duracao_min=365, n_paradas=1
        ),
        perna_volta=PernaVoo(
            cia="LATAM", saida_ts=dt("14:30", 12), chegada_ts=dt("20:10", 12), duracao_min=340, n_paradas=1
        ),
        comparacao_ow=ComparacaoOW(
            soma_oneways_centavos=365000, roundtrip_centavos=342000, roundtrip_mais_barato=True, diff_centavos=23000
        ),
        variante_flexivel=VarianteFlexivel(
            data_volta="2026-10-13",
            preco_centavos=329000,
            pct_mediana=-15.0,
            n_amostras=41,
            perna_volta=PernaVoo(
                cia="LATAM", saida_ts=dt("14:30", 13), chegada_ts=dt("20:10", 13), duracao_min=340, n_paradas=1
            ),
        ),
    )


def ctx_novo_minimo() -> RodadaContext:
    """Reproduz: 'NAT>BHZ | NOVO MINIMO R$ 2.980 (-19%)'"""
    base = ctx_rotina()
    base.preco_centavos = 298000
    base.pct_mediana = -19.0
    base.minimo_historico_centavos = 298000
    return base


def ctx_falha() -> FalhaContext:
    """Reproduz: 'NAT>BHZ | FALHA na coleta das 13:17'"""
    return FalhaContext(
        origem="NAT",
        destino="BHZ",
        rodada_hora_local="13:17",
        erro="RuntimeError: zero ofertas retornadas — tratar como falha da rodada\n(query: round_trip_1012, tentativa 2/2, backoff esgotado)",
    )


def main():
    enviar = "--send" in sys.argv

    casos = [
        ("rotina", *render_rotina(ctx_rotina())),
        ("novo_minimo", *render_alerta(ctx_novo_minimo(), motivo="novo_minimo")),
        ("falha", *render_falha(ctx_falha())),
    ]

    for nome, assunto, html in casos:
        out = ROOT / f"preview_{nome}.html"
        out.write_text(html)
        print(f"[{nome}] assunto: {assunto}")
        print(f"         preview salvo em {out.relative_to(ROOT)}")

    if not enviar:
        print("\n(rodando sem --send — nenhum e-mail foi enviado)")
        return

    env = carregar_env(ROOT / ".env")
    faltando = [k for k in ("GMAIL_USER", "GMAIL_APP_PASSWORD", "EMAIL_DESTINO") if not env.get(k)]
    if faltando:
        print(f"\nERRO: faltam no .env: {', '.join(faltando)}")
        sys.exit(1)

    print(f"\nEnviando 3 e-mails de teste para {env['EMAIL_DESTINO']}...")
    for nome, assunto, html in casos:
        send_email(
            gmail_user=env["GMAIL_USER"],
            gmail_app_password=env["GMAIL_APP_PASSWORD"],
            destino=env["EMAIL_DESTINO"],
            assunto=f"[TESTE-{nome}] {assunto}",
            html=html,
        )
        print(f"  enviado: [TESTE-{nome}] {assunto}")

    print("\nOk, confira sua caixa de entrada (e o spam, na primeira vez).")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Entrypoint do rastreador de preços (multi-rota, ver src/config.py).

Uso:
    python main.py                # rodada normal: roda todas as rotas ativas, grava, decide e manda e-mail
    python main.py --dry-run      # roda tudo (inclusive as consultas ao vivo), mas não grava nem envia
    python main.py --test-email   # manda 1 e-mail de exemplo com dados falsos, pra testar a conexão SMTP
"""

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "src"))


def carregar_dotenv_local() -> None:
    """Só pra conveniência rodando local — no GitHub Actions os secrets já
    chegam via variável de ambiente, então isso não faz nada lá (o .env
    nem existe no runner)."""
    env_path = ROOT / ".env"
    if not env_path.exists():
        return
    for linha in env_path.read_text().splitlines():
        linha = linha.strip()
        if linha and "=" in linha and not linha.startswith("#"):
            k, _, v = linha.partition("=")
            os.environ.setdefault(k.strip(), v.strip())


def cmd_test_email(gmail_user, gmail_app_password, email_destino) -> None:
    import emailer

    if not (gmail_user and gmail_app_password and email_destino):
        print("ERRO: faltam GMAIL_USER / GMAIL_APP_PASSWORD / EMAIL_DESTINO (env ou .env)")
        sys.exit(1)

    ctx = emailer.RodadaContext(
        origem="NAT", destino="MCZ", data_ida="2026-12-19", data_volta="2026-12-21",
        preco_centavos=342000, n_amostras=41, pct_mediana=-12.0,
        mediana_centavos=388000, minimo_historico_centavos=298000, media_centavos=401500.0,
        google_price_level="typical",
        perna_ida=emailer.PernaVoo(
            cia="LATAM",
            saida_ts=datetime(2026, 12, 19, 16, 0, tzinfo=timezone.utc),
            chegada_ts=datetime(2026, 12, 19, 22, 5, tzinfo=timezone.utc),
            duracao_min=365, n_paradas=1,
        ),
        perna_volta=emailer.PernaVoo(
            cia="LATAM",
            saida_ts=datetime(2026, 12, 21, 14, 30, tzinfo=timezone.utc),
            chegada_ts=datetime(2026, 12, 21, 20, 10, tzinfo=timezone.utc),
            duracao_min=340, n_paradas=1,
        ),
    )
    assunto, html = emailer.render_rotina(ctx)
    assunto = f"[TESTE] {assunto}"
    emailer.send_email(
        gmail_user=gmail_user, gmail_app_password=gmail_app_password,
        destino=email_destino, assunto=assunto, html=html,
    )
    print(f"E-mail de teste enviado: {assunto}")


def _rodar_rota(rota, *, gmail_user, gmail_app_password, serpapi_key, dry_run, forcar_rotina) -> dict:
    """Roda uma rota isolada com sua própria rede de segurança — um crash
    numa rota não pode impedir as outras de rodar, nem sair sem avisar
    ninguém."""
    import runner

    email_destino = os.environ.get(rota.email_env_var)

    try:
        resultado = runner.rodar(
            rota=rota,
            serpapi_key=serpapi_key,
            gmail_user=gmail_user,
            gmail_app_password=gmail_app_password,
            email_destino=email_destino,
            dry_run=dry_run,
            db_path=ROOT / "data" / "tracker.db",
            forcar_rotina=forcar_rotina,
        )
        return resultado
    except Exception:
        import traceback

        erro_completo = traceback.format_exc()
        print(f"[{rota.id}]\n{erro_completo}", file=sys.stderr)

        if not dry_run and gmail_user and gmail_app_password and email_destino:
            try:
                import emailer

                agora = datetime.now(timezone.utc)
                hora_local = agora.astimezone(emailer.FUSO_EXIBICAO).strftime("%H:%M")
                ctx = emailer.FalhaContext(
                    origem=rota.origem, destino=rota.destino, rodada_hora_local=hora_local,
                    erro=f"Crash não tratado no main.py (rota={rota.id}):\n\n{erro_completo[-3000:]}",
                    ts_utc=agora,
                )
                assunto, html = emailer.render_falha(ctx)
                emailer.send_email(
                    gmail_user=gmail_user, gmail_app_password=gmail_app_password,
                    destino=email_destino, assunto=assunto, html=html,
                )
                print(f"[{rota.id}] E-mail de falha (crash) enviado.", file=sys.stderr)
            except Exception as erro_email:
                print(f"[{rota.id}] Também falhou ao mandar e-mail de falha: {erro_email}", file=sys.stderr)

        return {"rota": rota.id, "crash": erro_completo}


def main() -> None:
    carregar_dotenv_local()

    ap = argparse.ArgumentParser(description="Rastreador de preços de passagem (multi-rota)")
    ap.add_argument("--dry-run", action="store_true", help="roda tudo sem gravar no banco nem enviar e-mail")
    ap.add_argument("--test-email", action="store_true", help="envia e-mail de exemplo com dados falsos")
    args = ap.parse_args()

    gmail_user = os.environ.get("GMAIL_USER")
    gmail_app_password = os.environ.get("GMAIL_APP_PASSWORD")
    serpapi_key = os.environ.get("SERPAPI_KEY")

    if args.test_email:
        cmd_test_email(gmail_user, gmail_app_password, os.environ.get("EMAIL_DESTINO"))
        return

    import config

    rodada_rotina_env = os.environ.get("RODADA_ROTINA")
    forcar_rotina = {"true": True, "false": False}.get((rodada_rotina_env or "").lower())

    resultados = {}
    houve_crash = False
    for rota in config.ROTAS:
        resultado = _rodar_rota(
            rota, gmail_user=gmail_user, gmail_app_password=gmail_app_password,
            serpapi_key=serpapi_key, dry_run=args.dry_run, forcar_rotina=forcar_rotina,
        )
        resultados[rota.id] = resultado
        if "crash" in resultado:
            houve_crash = True

    print(json.dumps(resultados, indent=2, ensure_ascii=False, default=str))
    if houve_crash:
        sys.exit(1)


if __name__ == "__main__":
    main()

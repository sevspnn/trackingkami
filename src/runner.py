"""Orquestra uma rodada completa: roda as 5 consultas, grava no banco,
calcula as métricas e decide se/qual e-mail mandar.

Decisão de design que vale registrar (não estava 100% explícita no spec):
mediana/mínimo/média que aparecem no e-mail e disparam os alertas são
sempre calculados sobre a série `mais_barato_da_rodada` (round_trip_1012),
porque é ela que corresponde ao preço mostrado ("o que você veria se
comprasse agora"). `itinerario_referencia` é uma série adicional, calculada
e disponível via queries.py assim que ITINERARIO_REFERENCIA for fixado,
mas hoje só é exibida como linha extra no e-mail — não gate alerta.
"""

from __future__ import annotations

import os
import random
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

import config
import db
import emailer
import fast_flights_client as ffc
import metrics
import queries as queries_mod

TIPO_PRIMARIO = "round_trip_1012"


def _sleep_jitter() -> None:
    time.sleep(random.uniform(config.JITTER_ENTRE_CONSULTAS_MIN_S, config.JITTER_ENTRE_CONSULTAS_MAX_S))


def _preco_min(resultado: dict) -> Optional[int]:
    if not resultado["ok"] or not resultado["ofertas"]:
        return None
    return min(o.preco_centavos for o in resultado["ofertas"])


def _oferta_mais_barata(resultado: dict):
    if not resultado["ok"] or not resultado["ofertas"]:
        return None
    return min(resultado["ofertas"], key=lambda o: o.preco_centavos)


def _perna_de_oferta(oferta) -> Optional[emailer.PernaVoo]:
    if oferta is None:
        return None
    return emailer.PernaVoo(
        cia=oferta.cia,
        saida_ts=datetime.fromisoformat(oferta.saida_ts) if oferta.saida_ts else None,
        chegada_ts=datetime.fromisoformat(oferta.chegada_ts) if oferta.chegada_ts else None,
        duracao_min=oferta.duracao_min,
        n_paradas=oferta.n_paradas,
    )


def _serie_historica(db_path: Path, tipo: str, *, conn=None) -> list[metrics.PricePoint]:
    if conn is not None:
        return queries_mod.serie_mais_barato_da_rodada(conn, tipo)
    if not os.path.exists(db_path):
        return []
    conn_leitura = db.connect(db_path)
    try:
        return queries_mod.serie_mais_barato_da_rodada(conn_leitura, tipo)
    finally:
        conn_leitura.close()


def _pode_enviar_imediato(conn, agora_utc: datetime) -> bool:
    ultima = db.ultima_notificacao_imediata(conn)
    if not ultima:
        return True
    ts_ultima = datetime.fromisoformat(ultima["ts_utc"])
    return (agora_utc - ts_ultima) >= timedelta(hours=config.ANTI_SPAM_HORAS)


def rodar(*, serpapi_key: Optional[str], gmail_user: Optional[str], gmail_app_password: Optional[str],
          email_destino: Optional[str], dry_run: bool, db_path: Path) -> dict:
    agora_utc = datetime.now(timezone.utc)
    hora_local = agora_utc.astimezone(emailer.FUSO_EXIBICAO)
    log = {"ts_utc": agora_utc.isoformat(), "dry_run": dry_run, "consultas": {}, "email_enviado": None}

    conn = None if dry_run else db.connect(db_path)
    if conn is not None:
        db.init_db(conn)

    queries = ffc.construir_queries()
    resultados = {}

    for i, spec in enumerate(queries):
        resultado = ffc.executar_consulta(spec, serpapi_key=serpapi_key)
        resultados[spec["tipo"]] = resultado
        log["consultas"][spec["tipo"]] = {
            "ok": resultado["ok"],
            "fonte": resultado["fonte"],
            "n_ofertas": len(resultado["ofertas"]),
            "erro": resultado["erro"],
        }

        if conn is not None:
            consulta_row = {
                "ts_utc": agora_utc.isoformat(),
                "tipo": spec["tipo"],
                "origem": config.ORIGEM,
                "destino": config.DESTINO,
                "data_ida": spec["data_ida"],
                "data_volta": spec["data_volta"],
                "ok": 1 if resultado["ok"] else 0,
                "erro": resultado["erro"],
                "fonte": resultado["fonte"],
                "google_price_level": resultado["google_price_level"],
                "google_typical_low": None,
                "google_typical_high": None,
            }
            consulta_id = db.insert_consulta(conn, consulta_row)
            for oferta in resultado["ofertas"]:
                db.insert_oferta(conn, consulta_id, oferta.as_row())
            conn.commit()

        if i < len(queries) - 1:
            _sleep_jitter()

    resultado_primario = resultados[TIPO_PRIMARIO]
    tipo_email: Optional[str] = None
    ctx_falha: Optional[emailer.FalhaContext] = None
    ctx_rodada: Optional[emailer.RodadaContext] = None

    if not resultado_primario["ok"]:
        tipo_email = "falha"
        ctx_falha = emailer.FalhaContext(
            origem=config.ORIGEM,
            destino=config.DESTINO,
            rodada_hora_local=hora_local.strftime("%H:%M"),
            erro=resultado_primario["erro"] or "erro desconhecido",
            ts_utc=agora_utc,
        )
    else:
        preco_atual = _preco_min(resultado_primario)
        serie_antes = _serie_historica(db_path, TIPO_PRIMARIO, conn=conn)
        if conn is not None and serie_antes:
            serie_antes = serie_antes[:-1]  # exclui a rodada que acabamos de inserir

        novo_minimo = metrics.eh_novo_minimo(preco_atual, serie_antes)
        queda = metrics.eh_queda_significativa(preco_atual, serie_antes, agora_utc)
        eh_rotina = hora_local.hour == config.EMAIL_ROTINA_HORA_LOCAL

        if novo_minimo:
            tipo_email = "novo_minimo"
        elif queda:
            tipo_email = "queda_significativa"
        elif eh_rotina:
            tipo_email = "rotina"

        if tipo_email is not None:
            serie_exibicao = serie_antes + [metrics.PricePoint(ts_utc=agora_utc, preco_centavos=preco_atual)]
            mediana = metrics.mediana_movel_14d(serie_exibicao, agora_utc)
            minimo = metrics.minimo_historico(serie_exibicao)
            media, n = metrics.media_geral(serie_exibicao)
            pct = metrics.variacao_pct(preco_atual, mediana) if len(serie_antes) >= metrics.MIN_AMOSTRAS_PARA_PORCENTAGEM else None

            preco_ida = _preco_min(resultados["one_way_ida"])
            preco_volta = _preco_min(resultados["one_way_volta_1012"])
            comparacao_ow = None
            if preco_ida is not None and preco_volta is not None:
                comp = metrics.comparar_oneways_vs_roundtrip(preco_ida, preco_volta, preco_atual)
                comparacao_ow = emailer.ComparacaoOW(
                    soma_oneways_centavos=comp.soma_oneways_centavos,
                    roundtrip_centavos=comp.roundtrip_centavos,
                    roundtrip_mais_barato=comp.roundtrip_mais_barato,
                    diff_centavos=comp.diff_centavos,
                )

            variante = None
            if config.VARIANTE_FLEXIVEL_ATIVA and resultados.get("round_trip_1013", {}).get("ok"):
                preco_1013 = _preco_min(resultados["round_trip_1013"])
                serie_1013 = _serie_historica(db_path, "round_trip_1013", conn=conn)
                pct_1013 = None
                if len(serie_1013) >= metrics.MIN_AMOSTRAS_PARA_PORCENTAGEM:
                    med_1013 = metrics.mediana_movel_14d(serie_1013, agora_utc)
                    pct_1013 = metrics.variacao_pct(preco_1013, med_1013)
                variante = emailer.VarianteFlexivel(
                    data_volta=config.DATA_VOLTA_FLEXIVEL,
                    preco_centavos=preco_1013,
                    pct_mediana=pct_1013,
                    n_amostras=len(serie_1013),
                    perna_volta=_perna_de_oferta(_oferta_mais_barata(resultados.get("one_way_volta_1013", {"ok": False, "ofertas": []}))),
                )

            ctx_rodada = emailer.RodadaContext(
                origem=config.ORIGEM,
                destino=config.DESTINO,
                data_ida=config.DATA_IDA,
                data_volta=config.DATA_VOLTA,
                preco_centavos=preco_atual,
                n_amostras=n,
                pct_mediana=pct,
                mediana_centavos=mediana,
                minimo_historico_centavos=minimo.preco_centavos if minimo else None,
                media_centavos=media,
                google_price_level=resultado_primario["google_price_level"],
                perna_ida=_perna_de_oferta(_oferta_mais_barata(resultado_primario)),
                perna_volta=_perna_de_oferta(_oferta_mais_barata(resultados["one_way_volta_1012"])),
                comparacao_ow=comparacao_ow,
                variante_flexivel=variante,
            )

    log["tipo_email"] = tipo_email

    if tipo_email and not dry_run and gmail_user and gmail_app_password and email_destino:
        pode_imediato = tipo_email in ("rotina", "falha") or _pode_enviar_imediato(conn, agora_utc)
        if pode_imediato:
            if tipo_email == "falha":
                assunto, html = emailer.render_falha(ctx_falha)
            elif tipo_email == "rotina":
                assunto, html = emailer.render_rotina(ctx_rodada)
            else:
                assunto, html = emailer.render_alerta(ctx_rodada, motivo=tipo_email)

            emailer.send_email(
                gmail_user=gmail_user, gmail_app_password=gmail_app_password,
                destino=email_destino, assunto=assunto, html=html,
            )
            db.insert_notificacao(conn, agora_utc.isoformat(), tipo_email)
            conn.commit()
            log["email_enviado"] = assunto
        else:
            log["email_enviado"] = f"suprimido por anti-spam (tipo={tipo_email})"
    elif tipo_email and dry_run:
        log["email_enviado"] = f"(dry-run) enviaria: tipo={tipo_email}"

    if conn is not None:
        conn.close()

    return log

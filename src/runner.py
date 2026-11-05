"""Orquestra uma rodada completa pra UMA rota: roda as consultas, grava no
banco, calcula as métricas e decide se/qual e-mail mandar. `main.py` chama
isso uma vez por rota ativa em `config.ROTAS`.

Decisão de design que vale registrar (não estava 100% explícita no spec):
mediana/mínimo/média que aparecem no e-mail e disparam os alertas são
sempre calculados sobre a série `mais_barato_da_rodada` (round_trip_principal),
porque é ela que corresponde ao preço mostrado ("o que você veria se
comprasse agora"). `itinerario_referencia` é uma série adicional, calculada
e disponível via queries.py assim que rota.itinerario_referencia for
fixado, mas hoje só é exibida como linha extra no e-mail — não gate alerta.
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
from config import RotaConfig

TIPO_PRIMARIO = "round_trip_principal"


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


def _serie_historica(db_path: Path, rota: RotaConfig, tipo: str = TIPO_PRIMARIO, *, conn=None) -> list[metrics.PricePoint]:
    def ler(c):
        if rota.dois_trechos:
            return queries_mod.serie_dois_trechos(c, rota.id)
        return queries_mod.serie_mais_barato_da_rodada(c, rota.id, tipo)

    if conn is not None:
        return ler(conn)
    if not os.path.exists(db_path):
        return []
    conn_leitura = db.connect(db_path)
    try:
        # Não chama db.init_db aqui de propósito: --dry-run não pode
        # escrever nada no arquivo, nem migração de schema. O banco real
        # é migrado pela primeira rodada não-dry-run (ou manualmente).
        return ler(conn_leitura)
    finally:
        conn_leitura.close()


def _pode_enviar_imediato(conn, rota_id: str, agora_utc: datetime) -> bool:
    ultima = db.ultima_notificacao_imediata(conn, rota_id)
    if not ultima:
        return True
    ts_ultima = datetime.fromisoformat(ultima["ts_utc"])
    return (agora_utc - ts_ultima) >= timedelta(hours=config.ANTI_SPAM_HORAS)


def _ja_enviou_rotina_hoje(conn, rota_id: str, hora_local: datetime) -> bool:
    """Evita duplicar o e-mail de rotina se, por atraso do agendador do
    Actions ou uma rodada manual de teste, duas execuções caírem no mesmo
    dia local (ver validação manual de 24/08/2026 — aconteceu de verdade)."""
    hoje = hora_local.date()
    rows = conn.execute(
        "SELECT ts_utc FROM notificacoes WHERE rota = ? AND tipo = 'rotina' ORDER BY ts_utc DESC LIMIT 5",
        (rota_id,),
    ).fetchall()
    return any(datetime.fromisoformat(r["ts_utc"]).astimezone(emailer.FUSO_EXIBICAO).date() == hoje for r in rows)


def _resumo_dois_trechos(rota: RotaConfig, queries: list[dict], resultados: dict) -> dict:
    """Junta as consultas one-way do modo dois trechos num 'resultado
    primário': preço = ida mais barata (entre as datas de ida) + volta mais
    barata (entre as datas de volta). Uma data falhar não derruba a rodada,
    desde que pelo menos um trecho de ida e um de volta tenham dado certo."""
    melhor = {}  # 'ida'/'volta' -> (preco, data, oferta)
    opcoes = []
    erros = []
    for spec in queries:
        eh_volta = spec["tipo"].startswith(ffc.TIPO_PREFIXO_TRECHO_VOLTA)
        lado = "volta" if eh_volta else "ida"
        data = spec["legs"][0][0]
        res = resultados[spec["tipo"]]
        oferta = _oferta_mais_barata(res)
        if oferta is None:
            erros.append(f"{lado} {data}: {res['erro'] or 'sem ofertas'}")
        elif lado not in melhor or oferta.preco_centavos < melhor[lado][0]:
            melhor[lado] = (oferta.preco_centavos, data, oferta, spec["tipo"])
        opcoes.append((lado, data, spec, oferta))

    ok = "ida" in melhor and "volta" in melhor
    opcoes_ctx = [
        emailer.OpcaoTrecho(
            rotulo=f"{'Volta' if lado == 'volta' else 'Ida'} {emailer.formatar_data_curta(data)} "
                   f"{spec['aeroporto_origem']}→{spec['aeroporto_destino']}",
            preco_centavos=oferta.preco_centavos if oferta else None,
            perna=_perna_de_oferta(oferta),
            escolhida=lado in melhor and melhor[lado][3] == spec["tipo"],
        )
        for lado, data, spec, oferta in opcoes
    ]
    return {
        "ok": ok,
        "erro": "; ".join(erros) or None,
        "preco": (melhor["ida"][0] + melhor["volta"][0]) if ok else None,
        "data_ida": melhor["ida"][1] if "ida" in melhor else rota.data_ida,
        "data_volta": melhor["volta"][1] if "volta" in melhor else rota.data_volta,
        "perna_ida": _perna_de_oferta(melhor["ida"][2]) if "ida" in melhor else None,
        "perna_volta": _perna_de_oferta(melhor["volta"][2]) if "volta" in melhor else None,
        # O nível do Google (low/typical/high) vem por consulta, ou seja, por
        # trecho avulso — não diz nada sobre a viagem combinada, então fica de fora.
        "google_price_level": None,
        "opcoes": opcoes_ctx,
    }


def rodar(*, rota: RotaConfig, serpapi_key: Optional[str], gmail_user: Optional[str],
          gmail_app_password: Optional[str], email_destino: Optional[str], dry_run: bool, db_path: Path,
          forcar_rotina: Optional[bool] = None) -> dict:
    """forcar_rotina: quando o chamador já sabe (pelo cron que disparou a
    execução) se essa é a rodada de rotina, passa True/False aqui em vez de
    deixar a gente inferir pela hora local — o agendador do GitHub Actions
    atrasa o disparo do cron com frequência (às vezes 20-40+ min, some com
    o jitter de propósito e pode empurrar a execução pra fora da hora
    'certa'), então inferir por `datetime.now().hour` depois do jitter é
    frágil. None (rodada manual/teste local) cai de volta nessa inferência."""
    agora_utc = datetime.now(timezone.utc)
    hora_local = agora_utc.astimezone(emailer.FUSO_EXIBICAO)
    log = {"rota": rota.id, "ts_utc": agora_utc.isoformat(), "dry_run": dry_run, "consultas": {}}

    conn = None if dry_run else db.connect(db_path)
    if conn is not None:
        db.init_db(conn)

    queries = ffc.construir_queries(rota)
    resultados = {}

    for i, spec in enumerate(queries):
        teto_atingido = conn is not None and db.contar_serpapi_desde(
            conn, (agora_utc - timedelta(hours=24)).isoformat()
        ) >= config.MAX_SERPAPI_POR_24H
        resultado = ffc.executar_consulta(spec, serpapi_key=serpapi_key, serpapi_teto_atingido=teto_atingido)
        resultados[spec["tipo"]] = resultado
        log["consultas"][spec["tipo"]] = {
            "ok": resultado["ok"],
            "fonte": resultado["fonte"],
            "n_ofertas": len(resultado["ofertas"]),
            "erro": resultado["erro"],
        }

        if conn is not None:
            consulta_row = {
                "rota": rota.id,
                "ts_utc": agora_utc.isoformat(),
                "tipo": spec["tipo"],
                # Modo dois trechos grava os aeroportos de cada trecho (a
                # volta não é o inverso da ida); o modo clássico mantém a
                # convenção histórica de sempre gravar os da rota.
                "origem": spec["aeroporto_origem"] if rota.dois_trechos else rota.origem,
                "destino": spec["aeroporto_destino"] if rota.dois_trechos else rota.destino,
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

    if rota.dois_trechos:
        resumo = _resumo_dois_trechos(rota, queries, resultados)
        primario_ok, primario_erro = resumo["ok"], resumo["erro"]
        log["preco_combinado"] = resumo["preco"]
    else:
        resultado_primario = resultados[TIPO_PRIMARIO]
        primario_ok, primario_erro = resultado_primario["ok"], resultado_primario["erro"]
    tipos_email: list[str] = []
    ctx_falha: Optional[emailer.FalhaContext] = None
    ctx_rodada: Optional[emailer.RodadaContext] = None

    if not primario_ok:
        tipos_email = ["falha"]
        ctx_falha = emailer.FalhaContext(
            origem=rota.origem,
            destino=rota.destino,
            rodada_hora_local=hora_local.strftime("%H:%M"),
            erro=primario_erro or "erro desconhecido",
            ts_utc=agora_utc,
            rotulo=rota.rotulo,
        )
    else:
        if rota.dois_trechos:
            preco_atual = resumo["preco"]
        else:
            preco_atual = _preco_min(resultado_primario)
        serie_antes = _serie_historica(db_path, rota, conn=conn)
        if conn is not None and serie_antes:
            serie_antes = serie_antes[:-1]  # exclui a rodada que acabamos de inserir

        novo_minimo = metrics.eh_novo_minimo(preco_atual, serie_antes)
        queda = metrics.eh_queda_significativa(preco_atual, serie_antes, agora_utc)
        eh_rotina = forcar_rotina if forcar_rotina is not None else hora_local.hour == config.EMAIL_ROTINA_HORA_LOCAL

        # Rotina e alerta imediato são políticas independentes (ver spec):
        # se a rodada das 07:17 também for um novo mínimo/queda, manda os
        # dois — um não deve engolir o outro (bug real observado em
        # 25-27/08/2026: dias com queda de preço silenciosamente perderam
        # o e-mail de rotina porque isso era um elif).
        if novo_minimo:
            tipos_email.append("novo_minimo")
        elif queda:
            tipos_email.append("queda_significativa")
        if eh_rotina:
            tipos_email.append("rotina")

        if tipos_email:
            serie_exibicao = serie_antes + [metrics.PricePoint(ts_utc=agora_utc, preco_centavos=preco_atual)]
            mediana = metrics.mediana_movel_14d(serie_exibicao, agora_utc)
            minimo = metrics.minimo_historico(serie_exibicao)
            media, n = metrics.media_geral(serie_exibicao)
            pct = metrics.variacao_pct(preco_atual, mediana) if len(serie_antes) >= metrics.MIN_AMOSTRAS_PARA_PORCENTAGEM else None

            if rota.dois_trechos:
                ctx_rodada = emailer.RodadaContext(
                    origem=rota.origem,
                    destino=rota.destino,
                    data_ida=resumo["data_ida"],
                    data_volta=resumo["data_volta"],
                    preco_centavos=preco_atual,
                    n_amostras=n,
                    pct_mediana=pct,
                    mediana_centavos=mediana,
                    minimo_historico_centavos=minimo.preco_centavos if minimo else None,
                    media_centavos=media,
                    google_price_level=resumo["google_price_level"],
                    perna_ida=resumo["perna_ida"],
                    perna_volta=resumo["perna_volta"],
                    rotulo=rota.rotulo,
                    trecho_ida=f"{rota.origem}→{rota.destino}",
                    trecho_volta=f"{rota.volta_origem}→{rota.volta_destino}",
                    opcoes_trechos=resumo["opcoes"],
                )

        if tipos_email and not rota.dois_trechos:
            preco_ida = _preco_min(resultados["one_way_ida"])
            preco_volta = _preco_min(resultados["one_way_volta_principal"])
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
            if rota.variante_flexivel_ativa and resultados.get("round_trip_flexivel", {}).get("ok"):
                preco_flex = _preco_min(resultados["round_trip_flexivel"])
                serie_flex = _serie_historica(db_path, rota, "round_trip_flexivel", conn=conn)
                pct_flex = None
                if len(serie_flex) >= metrics.MIN_AMOSTRAS_PARA_PORCENTAGEM:
                    med_flex = metrics.mediana_movel_14d(serie_flex, agora_utc)
                    pct_flex = metrics.variacao_pct(preco_flex, med_flex)
                variante = emailer.VarianteFlexivel(
                    data_volta=rota.data_volta_flexivel,
                    preco_centavos=preco_flex,
                    pct_mediana=pct_flex,
                    n_amostras=len(serie_flex),
                    perna_volta=_perna_de_oferta(_oferta_mais_barata(resultados.get("one_way_volta_flexivel", {"ok": False, "ofertas": []}))),
                )

            ctx_rodada = emailer.RodadaContext(
                origem=rota.origem,
                destino=rota.destino,
                data_ida=rota.data_ida,
                data_volta=rota.data_volta,
                preco_centavos=preco_atual,
                n_amostras=n,
                pct_mediana=pct,
                mediana_centavos=mediana,
                minimo_historico_centavos=minimo.preco_centavos if minimo else None,
                media_centavos=media,
                google_price_level=resultado_primario["google_price_level"],
                perna_ida=_perna_de_oferta(_oferta_mais_barata(resultado_primario)),
                perna_volta=_perna_de_oferta(_oferta_mais_barata(resultados["one_way_volta_principal"])),
                comparacao_ow=comparacao_ow,
                variante_flexivel=variante,
            )

    log["tipos_email"] = tipos_email
    log["emails_enviados"] = []

    if tipos_email and not dry_run and gmail_user and gmail_app_password and email_destino:
        for tipo in tipos_email:
            if tipo == "rotina":
                pode_enviar = not _ja_enviou_rotina_hoje(conn, rota.id, hora_local)
            elif tipo == "falha":
                pode_enviar = True
            else:
                pode_enviar = _pode_enviar_imediato(conn, rota.id, agora_utc)

            if not pode_enviar:
                log["emails_enviados"].append(f"suprimido por anti-spam (tipo={tipo})")
                continue

            if tipo == "falha":
                assunto, html = emailer.render_falha(ctx_falha)
            elif tipo == "rotina":
                assunto, html = emailer.render_rotina(ctx_rodada)
            else:
                assunto, html = emailer.render_alerta(ctx_rodada, motivo=tipo)

            emailer.send_email(
                gmail_user=gmail_user, gmail_app_password=gmail_app_password,
                destino=email_destino, assunto=assunto, html=html,
            )
            db.insert_notificacao(conn, rota.id, agora_utc.isoformat(), tipo)
            conn.commit()
            log["emails_enviados"].append(assunto)
    elif tipos_email and dry_run:
        log["emails_enviados"] = [f"(dry-run) enviaria: tipo={t}" for t in tipos_email]

    if conn is not None:
        conn.close()

    return log

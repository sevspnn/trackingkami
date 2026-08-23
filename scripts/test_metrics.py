"""Teste manual da etapa 3: métricas com dados sintéticos (sem tocar rede/banco real)."""

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from metrics import (
    PricePoint,
    comparar_oneways_vs_roundtrip,
    eh_novo_minimo,
    eh_queda_significativa,
    eh_variacao_suspeita,
    formatar_comparacao_mediana,
    media_geral,
    mediana_movel_14d,
    minimo_historico,
)

NOW = datetime(2026, 9, 20, tzinfo=timezone.utc)


def pontos_ultimos_dias(precos_reais: list[float], dias_atras_inicial: int = 20) -> list[PricePoint]:
    """Gera 1 ponto por dia, do mais antigo ao mais recente, terminando em NOW."""
    n = len(precos_reais)
    return [
        PricePoint(
            ts_utc=NOW - timedelta(days=(n - 1 - i)),
            preco_centavos=round(preco * 100),
        )
        for i, preco in enumerate(precos_reais)
    ]


def check(label, got, expected):
    status = "OK " if got == expected else "FALHOU"
    print(f"[{status}] {label}: got={got!r} expected={expected!r}")
    assert got == expected, f"{label}: {got!r} != {expected!r}"


def main():
    # --- caso 1: n < 8 amostras -> mensagem de coleta, não porcentagem ---
    poucos = pontos_ultimos_dias([3000, 3100, 2950, 3200, 3050])
    msg = formatar_comparacao_mediana(3100_00, poucos, NOW)
    check("n<8 mostra coleta", msg, "coletando histórico (n=5)")

    # --- caso 2: série de 20 dias, mediana móvel de 14d, comparação % ---
    # últimos 14 dias (dias -13..0): valores conhecidos p/ mediana
    serie_20d = pontos_ultimos_dias(
        # 20 pontos, do mais antigo pro mais recente
        [3500, 3500, 3500, 3500, 3500, 3500,  # dias -19..-14 (fora da janela de 14d)
         3000, 3050, 2900, 3100, 3200, 2950, 3000, 3100, 3050, 3000, 2980, 3020, 3000, 3000]
        # dias -13..0 (14 valores, dentro da janela)
    )
    janela_14d_esperada = sorted([3000, 3050, 2900, 3100, 3200, 2950, 3000, 3100, 3050, 3000, 2980, 3020, 3000, 3000])
    mediana_esperada_centavos = round(sorted(janela_14d_esperada)[len(janela_14d_esperada) // 2] * 100 -
                                       (0 if len(janela_14d_esperada) % 2 else 0))
    # mediana de lista par -> usar statistics.median direto pra conferir
    import statistics
    mediana_esperada = round(statistics.median([v * 100 for v in janela_14d_esperada]))
    med = mediana_movel_14d(serie_20d, NOW)
    check("mediana móvel 14d ignora pontos fora da janela", med, mediana_esperada)

    preco_atual = 2700_00  # queda forte
    pct_esperado = round((preco_atual - mediana_esperada) / mediana_esperada * 100)
    msg2 = formatar_comparacao_mediana(preco_atual, serie_20d, NOW)
    print(f"  -> mensagem: {msg2} (pct bruto esperado ~{pct_esperado}%)")
    assert msg2.endswith("% mediana")

    # --- caso 3: mínimo histórico ---
    minimo = minimo_historico(serie_20d)
    check("mínimo histórico = 2900 (em centavos)", minimo.preco_centavos, 2900_00)

    # --- caso 4: média geral sempre com n ---
    media, n = media_geral(serie_20d)
    check("media_geral n", n, 20)
    print(f"  -> média = R$ {media/100:,.2f} (n={n})")

    # --- caso 5: novo mínimo histórico ---
    check("2500 é novo mínimo (abaixo de 2900)", eh_novo_minimo(2500_00, serie_20d), True)
    check("2950 NÃO é novo mínimo (2900 já existe)", eh_novo_minimo(2950_00, serie_20d), False)

    # --- caso 6: queda significativa (>8% abaixo da mediana 14d) ---
    limiar_preco = round(mediana_esperada * 0.92)  # exatamente -8%
    check("queda de exatos -8% NÃO dispara (regra é '>8%')", eh_queda_significativa(limiar_preco, serie_20d, NOW), False)
    check("queda de -15% dispara", eh_queda_significativa(round(mediana_esperada * 0.85), serie_20d, NOW), True)
    check("alta de preço não dispara queda", eh_queda_significativa(round(mediana_esperada * 1.20), serie_20d, NOW), False)

    # --- caso 7: variação suspeita entre rodadas consecutivas (>60%) ---
    check("dobrar de preço entre rodadas é suspeito", eh_variacao_suspeita(6000_00, 3000_00), True)
    check("cair 70% entre rodadas é suspeito", eh_variacao_suspeita(900_00, 3000_00), True)
    check("variação de 10% não é suspeita", eh_variacao_suspeita(3300_00, 3000_00), False)
    check("sem rodada anterior não avalia", eh_variacao_suspeita(3000_00, None), False)

    # --- caso 8: comparação 2x one-way vs round-trip ---
    comp = comparar_oneways_vs_roundtrip(
        preco_ida_centavos=900_00, preco_volta_centavos=950_00, preco_roundtrip_centavos=1762_00
    )
    check("soma one-ways", comp.soma_oneways_centavos, 1850_00)
    check("round-trip mais barato", comp.roundtrip_mais_barato, True)
    print(f"  -> {comp.resumo()}")

    print("\nTodos os testes sintéticos passaram.")


if __name__ == "__main__":
    main()

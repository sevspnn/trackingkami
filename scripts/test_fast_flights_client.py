"""Teste manual: roda as 5 consultas reais (round_trip_1012, one_way_ida,
one_way_volta_1012, round_trip_1013, one_way_volta_1013) com o jitter entre
elas, igual vai rodar em produção. Não grava no banco, só imprime.
"""

import random
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import config
import fast_flights_client as ffc


def carregar_env(path: Path) -> dict:
    env = {}
    if path.exists():
        for linha in path.read_text().splitlines():
            linha = linha.strip()
            if linha and "=" in linha and not linha.startswith("#"):
                k, _, v = linha.partition("=")
                env[k.strip()] = v.strip()
    return env


def main():
    env = carregar_env(Path(__file__).resolve().parent.parent / ".env")
    serpapi_key = env.get("SERPAPI_KEY")

    queries = ffc.construir_queries()
    print(f"{len(queries)} consultas a rodar\n")

    for i, spec in enumerate(queries):
        t0 = time.time()
        resultado = ffc.executar_consulta(spec, serpapi_key=serpapi_key)
        dt = time.time() - t0

        status = "OK" if resultado["ok"] else "FALHA"
        n = len(resultado["ofertas"])
        mais_barato = min((o.preco_centavos for o in resultado["ofertas"]), default=None)
        mais_barato_txt = f"R$ {mais_barato/100:,.2f}" if mais_barato else "-"

        print(
            f"[{status}] {spec['tipo']:<20} fonte={resultado['fonte']:<16} "
            f"n_ofertas={n:<4} mais_barato={mais_barato_txt:<12} {dt:.1f}s"
        )
        if resultado["erro"]:
            print(f"         erro/aviso: {resultado['erro']}")

        if i < len(queries) - 1:
            espera = random.uniform(config.JITTER_ENTRE_CONSULTAS_MIN_S, config.JITTER_ENTRE_CONSULTAS_MAX_S)
            print(f"         (aguardando {espera:.1f}s...)")
            time.sleep(espera)

    print("\nFim.")


if __name__ == "__main__":
    main()

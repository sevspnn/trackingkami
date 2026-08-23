"""Teste manual da etapa 5: chama o SerpApi de verdade, importa price_history
pra dentro de data/tracker.db (banco real do projeto, não o de teste).

Uso: python scripts/bootstrap_serpapi.py
"""

import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import db
from serpapi_client import SerpApiError, buscar_price_insights

ROOT = Path(__file__).resolve().parent.parent
DB_PATH = ROOT / "data" / "tracker.db"


def carregar_env(path: Path) -> dict:
    env = {}
    for linha in path.read_text().splitlines():
        linha = linha.strip()
        if linha and "=" in linha and not linha.startswith("#"):
            k, _, v = linha.partition("=")
            env[k.strip()] = v.strip()
    return env


def main():
    env = carregar_env(ROOT / ".env")
    api_key = env.get("SERPAPI_KEY")
    if not api_key:
        print("SERPAPI_KEY não configurada no .env — nada a fazer (comportamento esperado do projeto).")
        return

    print("Buscando price_insights (NAT -> CNF, 2026-10-08 / 2026-10-12)...")
    try:
        pi = buscar_price_insights(
            api_key=api_key, origem="NAT", data_ida="2026-10-08", data_volta="2026-10-12"
        )
    except SerpApiError as e:
        print(f"FALHA: {e}")
        sys.exit(1)

    print(f"lowest_price: R$ {pi.lowest_price_centavos/100:,.2f}")
    print(f"price_level: {pi.price_level}")
    print(f"typical_price_range: R$ {pi.typical_low_centavos/100:,.2f} - R$ {pi.typical_high_centavos/100:,.2f}")
    print(f"price_history: {len(pi.price_history)} pontos")

    conn = db.connect(DB_PATH)
    db.init_db(conn)

    agora = datetime.now(timezone.utc).isoformat()
    antes = conn.execute("SELECT COUNT(*) FROM historico_google").fetchone()[0]
    for ts_utc, preco_centavos in pi.price_history:
        db.insert_historico_google(conn, ts_utc.isoformat(), preco_centavos, agora)
    conn.commit()
    depois = conn.execute("SELECT COUNT(*) FROM historico_google").fetchone()[0]

    print(f"\nhistorico_google: {antes} -> {depois} linhas ({depois - antes} inseridas)")

    amostra = conn.execute(
        "SELECT ts_utc, preco_centavos FROM historico_google ORDER BY ts_utc DESC LIMIT 5"
    ).fetchall()
    print("\n5 mais recentes:")
    for r in amostra:
        print(f"  {r['ts_utc']}  R$ {r['preco_centavos']/100:,.2f}")

    conn.close()


if __name__ == "__main__":
    main()

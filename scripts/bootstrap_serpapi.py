"""Bootstrap/recalibração do histórico via SerpApi pra uma rota — importa
price_history pra dentro de data/tracker.db (banco real do projeto).

Uso: python scripts/bootstrap_serpapi.py [rota_id]
     (rota_id default: primeira rota ativa em config.ROTAS)
"""

import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import config
import db
from fast_flights_client import _serpapi_airport
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

    rota_id = sys.argv[1] if len(sys.argv) > 1 else config.ROTAS[0].id
    rota = next(r for r in config.ROTAS if r.id == rota_id)
    origem_serpapi = _serpapi_airport(rota.origem)
    destino_serpapi = _serpapi_airport(rota.destino)

    print(f"Buscando price_insights ({origem_serpapi} -> {destino_serpapi}, {rota.data_ida} / {rota.data_volta})...")
    try:
        pi = buscar_price_insights(
            api_key=api_key, origem=origem_serpapi, destino=destino_serpapi,
            data_ida=rota.data_ida, data_volta=rota.data_volta,
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
    antes = conn.execute("SELECT COUNT(*) FROM historico_google WHERE rota = ?", (rota.id,)).fetchone()[0]
    for ts_utc, preco_centavos in pi.price_history:
        db.insert_historico_google(conn, rota.id, ts_utc.isoformat(), preco_centavos, agora)
    conn.commit()
    depois = conn.execute("SELECT COUNT(*) FROM historico_google WHERE rota = ?", (rota.id,)).fetchone()[0]

    print(f"\nhistorico_google[{rota.id}]: {antes} -> {depois} linhas ({depois - antes} novas/atualizadas)")

    amostra = conn.execute(
        "SELECT ts_utc, preco_centavos FROM historico_google WHERE rota = ? ORDER BY ts_utc DESC LIMIT 5",
        (rota.id,),
    ).fetchall()
    print("\n5 mais recentes:")
    for r in amostra:
        print(f"  {r['ts_utc']}  R$ {r['preco_centavos']/100:,.2f}")

    conn.close()


if __name__ == "__main__":
    main()

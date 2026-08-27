"""Teste manual da etapa 2: consulta ao vivo -> parser -> grava em banco de
teste -> relê e imprime, pra conferir visualmente que os dados batem com o
que a etapa 1 mostrou cru no terminal.

Uso: python scripts/test_parser.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from datetime import datetime, timezone

from fast_flights import FlightData, Passengers, TFSData, get_flights_from_filter

import db
import parser as parser_mod

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "test.db"


def main():
    DB_PATH.unlink(missing_ok=True)
    conn = db.connect(DB_PATH)
    db.init_db(conn)

    # Consulta 1: round-trip 08/10 + 12/10
    filt = TFSData.from_interface(
        flight_data=[
            FlightData(date="2026-10-08", from_airport="NAT", to_airport="BHZ"),
            FlightData(date="2026-10-12", from_airport="BHZ", to_airport="NAT"),
        ],
        trip="round-trip",
        passengers=Passengers(adults=1),
        seat="economy",
    )
    result = get_flights_from_filter(filt, currency="BRL", mode="common")

    ofertas = parser_mod.parse_result(
        result,
        reference_date="2026-10-08",
        aeroporto_origem="NAT",
        aeroporto_destino="BHZ",
    )

    consulta_row = {
        "rota": "teste_manual",
        "ts_utc": datetime.now(timezone.utc).isoformat(),
        "tipo": "round_trip_1012",
        "origem": "NAT",
        "destino": "BHZ",
        "data_ida": "2026-10-08",
        "data_volta": "2026-10-12",
        "ok": 1,
        "erro": None,
        "fonte": "fast_flights",
        "google_price_level": result.current_price,
        "google_typical_low": None,
        "google_typical_high": None,
    }
    consulta_id = db.insert_consulta(conn, consulta_row)
    for o in ofertas:
        db.insert_oferta(conn, consulta_id, o.as_row())
    conn.commit()

    print(f"Consulta id={consulta_id} | current_price={result.current_price} | {len(ofertas)} ofertas gravadas\n")

    rows = conn.execute(
        """
        SELECT cia, preco_centavos, moeda, is_best, saida_ts, chegada_ts,
               duracao_min, n_paradas
        FROM ofertas
        WHERE consulta_id = ?
        ORDER BY preco_centavos ASC
        LIMIT 10
        """,
        (consulta_id,),
    ).fetchall()

    print("10 mais baratas (de", len(ofertas), "no total):")
    for r in rows:
        preco_reais = r["preco_centavos"] / 100
        best = "★" if r["is_best"] else " "
        print(
            f"  {best} {r['cia']:<8} R${preco_reais:>8,.2f}  "
            f"{r['saida_ts']}  ->  {r['chegada_ts']}  "
            f"{r['duracao_min']}min  {r['n_paradas']} paradas"
        )

    # sanity check: soma de centavos bate com o que a lib reportou como string
    barata = rows[0]
    print(f"\nSanity: menor preço = R$ {barata['preco_centavos']/100:,.2f}")

    conn.close()


if __name__ == "__main__":
    main()

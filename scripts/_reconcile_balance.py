"""One-off reconciliation: CSV vs DB vs Betfair balance."""
from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.import_betfair_csv import COMMISSION, csv_outcome, extract_betfair_id, market_key, parse_money
from palpitaria.database import SessionLocal
from palpitaria.models import Bet, Branch, User
from palpitaria.services.ledger import betfair_csv_net_pl, is_betfair_imported_bet


def main() -> None:
    csv_path = Path(r"c:\Users\Usuário\Downloads\ExchangeBets_Settled (2).csv")
    rows = []
    with csv_path.open(encoding="utf-8-sig", newline="") as f:
        import csv

        for row in csv.DictReader(f):
            desc = row["Descrição"]
            stake = parse_money(row["Valor Apostado (R$)"])
            pl_raw = parse_money(row["Lucro/Perda"])
            odds = parse_money(row["Cotações"])
            side = "LAY" if row["Tipo"].strip().lower().startswith("contra") else "BACK"
            if market_key(desc) == "lay_cs":
                side = "LAY"
            outcome = csv_outcome(row["Status"].strip(), pl_raw)
            net = (
                betfair_csv_net_pl(stake or 0, odds or 1, outcome, COMMISSION, side=side)
                if stake and odds
                else 0.0
            )
            rows.append(
                {
                    "pl_csv": pl_raw or 0,
                    "net": net,
                    "resolved": row["Resolvida"][:8].lower(),
                    "bf_id": extract_betfair_id(desc),
                }
            )

    sum_csv = round(sum(r["pl_csv"] for r in rows), 2)
    sum_net = round(sum(r["net"] for r in rows), 2)
    print(f"CSV rows: {len(rows)}")
    print(f"CSV Lucro/Perda (coluna): R$ {sum_csv:.2f}")
    print(f"CSV recalculado líquido 6,5%: R$ {sum_net:.2f}")
    print(f"Diferenca bruto -> liquido: R$ {sum_csv - sum_net:.2f}")

    jul = [r for r in rows if "-jul-" in r["resolved"]]
    print(
        f"Julho (resolvida): {len(jul)} apostas | "
        f"csv R$ {sum(r['pl_csv'] for r in jul):.2f} | net R$ {sum(r['net'] for r in jul):.2f}"
    )

    db = SessionLocal()
    user = db.query(User).filter_by(email="nelson.r.furlan@gmail.com").first()
    bets = db.query(Bet).join(Branch).filter(Branch.user_id == user.id).all()
    db_pl = round(sum(b.profit_loss for b in bets), 2)
    bf_bets = [b for b in bets if is_betfair_imported_bet(b)]

    print(f"\nDB apostas: {len(bets)} | P&L total: R$ {db_pl:.2f}")
    print(f"DB import BF: {len(bf_bets)} | P&L BF: R$ {round(sum(b.profit_loss for b in bf_bets), 2):.2f}")
    print(f"Depósitos: R$ {user.total_deposits:.2f} | Saques: R$ {user.total_withdrawals:.2f}")
    saldo = user.total_deposits - user.total_withdrawals + db_pl
    print(f"Saldo sistema: R$ {saldo:.2f}")
    betfair = 626.64
    print(f"Betfair (print): R$ {betfair:.2f}")
    print(f"Gap saldo: R$ {saldo - betfair:.2f}")
    print(f"P&L implícito Betfair (dep {user.total_deposits:.0f}): R$ {betfair - user.total_deposits:.2f}")

    csv_ids = {r["bf_id"] for r in rows if r["bf_id"]}
    db_by_id: dict[str, Bet] = {}
    for b in bf_bets:
        m = re.search(r"\[BF:(\d+)\]", b.description or "")
        if m:
            db_by_id[m.group(1)] = b
    only_csv = csv_ids - set(db_by_id)
    only_db = set(db_by_id) - csv_ids
    print(f"\nIDs CSV: {len(csv_ids)} | DB BF: {len(db_by_id)}")
    print(f"Só no CSV: {len(only_csv)} | Só no DB: {len(only_db)}")

    mism = []
    for r in rows:
        bid = r["bf_id"]
        if not bid:
            continue
        b = db_by_id.get(bid)
        if not b:
            mism.append((bid, "ausente_db", r["net"], r["pl_csv"]))
        elif abs(b.profit_loss - r["net"]) > 0.02:
            mism.append((bid, "pl_diff", r["net"], b.profit_loss))
    print(f"Divergências P&L: {len(mism)}")
    for item in mism[:10]:
        print(" ", item)

    # non-BF bets in DB
    manual = [b for b in bets if not is_betfair_imported_bet(b)]
    if manual:
        print(f"\nApostas NÃO importadas ({len(manual)}):")
        for b in manual:
            print(f"  id={b.id} pl={b.profit_loss:.2f} {b.description[:70]}")

    db.close()


if __name__ == "__main__":
    main()

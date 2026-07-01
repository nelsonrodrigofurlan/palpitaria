"""Remove CSV import bets and restore June 2026 summaries to pre-import state."""

from __future__ import annotations

from datetime import datetime

from palpitaria.database import SessionLocal
from palpitaria.models import Bet, Branch, BranchMonthlySummary, User

USER_EMAIL = "nelson.r.furlan@gmail.com"
COMP = "WC"
YEAR, MONTH = 2026, 6

# Consolidado de junho antes do import via CSV (nelson.r.furlan@gmail.com)
PRE_IMPORT: dict[int, dict] = {
    1: {"bet_count": 6, "win_count": 1, "loss_count": 5, "pending_count": 0, "total_pl": -159.79, "total_stake": 600.0},
    2: {"bet_count": 29, "win_count": 12, "loss_count": 17, "pending_count": 0, "total_pl": -102.73, "total_stake": 2900.0},
    3: {"bet_count": 13, "win_count": 9, "loss_count": 4, "pending_count": 0, "total_pl": 77.74, "total_stake": 1270.0},
    16: {"bet_count": 1, "win_count": 1, "loss_count": 0, "pending_count": 0, "total_pl": 5.61, "total_stake": 6.0},
    20: {"bet_count": 1, "win_count": 1, "loss_count": 0, "pending_count": 0, "total_pl": 9.72, "total_stake": 20.0},
    21: {"bet_count": 7, "win_count": 3, "loss_count": 4, "pending_count": 0, "total_pl": -82.19, "total_stake": 700.0},
    23: {"bet_count": 3, "win_count": 0, "loss_count": 3, "pending_count": 0, "total_pl": -92.6, "total_stake": 59.42},
    24: {"bet_count": 1, "win_count": 0, "loss_count": 1, "pending_count": 0, "total_pl": -65.2, "total_stake": 65.2},
    25: {"bet_count": 8, "win_count": 7, "loss_count": 1, "pending_count": 0, "total_pl": 256.31, "total_stake": 800.0},
}


def main() -> None:
    db = SessionLocal()
    user = db.query(User).filter(User.email == USER_EMAIL).first()
    if not user:
        raise SystemExit(f"User not found: {USER_EMAIL}")

    imported = (
        db.query(Bet)
        .join(Branch)
        .filter(Branch.user_id == user.id, Bet.description.contains("[BF:"))
        .all()
    )
    for bet in imported:
        db.delete(bet)

    restored = 0
    for branch_id, data in PRE_IMPORT.items():
        summary = (
            db.query(BranchMonthlySummary)
            .filter_by(branch_id=branch_id, year=YEAR, month=MONTH, competition_code=COMP)
            .one_or_none()
        )
        if not summary:
            continue
        for key, value in data.items():
            setattr(summary, key, value)
        restored += 1

    db.commit()

    remaining = db.query(Bet).join(Branch).filter(Branch.user_id == user.id).count()
    sum_pl = (
        db.query(BranchMonthlySummary)
        .join(Branch)
        .filter(Branch.user_id == user.id, BranchMonthlySummary.year == YEAR, BranchMonthlySummary.month == MONTH)
        .all()
    )
    total = round(sum(s.total_pl for s in sum_pl), 2)
    deposits = user.total_deposits - user.total_withdrawals

    print(f"Removidas {len(imported)} entradas importadas do CSV.")
    print(f"Restaurados {restored} consolidados de junho/2026.")
    print(f"Apostas ativas restantes: {remaining}")
    print(f"P&L junho (summaries): R$ {total:,.2f}")
    print(f"Saldo histórico (depósitos + P&L): R$ {deposits + total:,.2f}")
    db.close()


if __name__ == "__main__":
    main()

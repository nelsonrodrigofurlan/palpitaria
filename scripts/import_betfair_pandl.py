"""Import Betfair BettingPandL.csv (market-level) into filiais."""
from __future__ import annotations

import argparse
import csv
import hashlib
import re
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from palpitaria.config import settings
from palpitaria.database import SessionLocal
from palpitaria.models import Bet, Branch, BranchMonthlySummary, User
from palpitaria.services.ledger import bet_in_period

USER_EMAIL = "nelson.r.furlan@gmail.com"
COMP_CODE = "WC"
PANDL_TAG = "[PandL:"

MONTHS_EN = {
    "jan": 1,
    "feb": 2,
    "mar": 3,
    "apr": 4,
    "may": 5,
    "jun": 6,
    "jul": 7,
    "aug": 8,
    "sep": 9,
    "oct": 10,
    "nov": 11,
    "dec": 12,
}


def parse_money(raw: str) -> float:
    return float((raw or "0").strip().replace(" ", "").replace(",", "."))


def parse_bf_dt(raw: str) -> datetime:
    parts = raw.strip().split()
    date_part = parts[0]
    time_part = parts[1] if len(parts) > 1 else "12:00"
    day_s, mon_s, yr_s = date_part.split("-")
    year = 2000 + int(yr_s)
    month = MONTHS_EN[mon_s.lower()[:3]]
    day = int(day_s)
    if ":" in time_part:
        bits = [int(x) for x in time_part.split(":")]
        h, mi = bits[0], bits[1]
        sec = bits[2] if len(bits) > 2 else 0
    else:
        h = mi = sec = 0
    local = datetime(year, month, day, h, mi, sec, tzinfo=ZoneInfo(settings.app_timezone))
    return local.astimezone(ZoneInfo("UTC")).replace(tzinfo=None)


def market_key(market: str) -> str:
    selection = market.split(" : ", 1)[1] if " : " in market else market
    s = selection.lower()
    if "over/under 0.5" in s or "over/under 0,5" in s:
        return "over_0_5"
    if "over/under 1.5" in s or "over/under 1,5" in s:
        return "over_1_5"
    if "over/under 2.5" in s or "over/under 2,5" in s:
        return "over_2_5"
    if "over/under" in s and ("4.5" in s or "4,5" in s):
        return "under_4_5"
    if "match odds" in s:
        return "1x2"
    if "correct score" in s:
        return "lay_cs"
    if "shots on target" in s or "player" in s:
        return "shots"
    if re.search(r"[+-]\d", selection):
        return "ah"
    return "other"


def resolve_branch(key: str, by_slug: dict[str, Branch], user_id: int) -> Branch | None:
    if key == "over_0_5":
        return by_slug.get("over_0_5")
    if key == "over_1_5":
        return by_slug.get("over_1_5")
    if key == "over_2_5":
        return by_slug.get("over_2.5_gols_1")
    if key == "under_4_5":
        return by_slug.get("under_4,5_gols_1")
    if key == "1x2":
        return by_slug.get("1x2")
    if key == "lay_cs":
        return by_slug.get("correct_score_1")
    if key == "ah":
        return by_slug.get(f"handicap_ah_{user_id}") or by_slug.get("handicap_ah_1")
    if key == "shots":
        return by_slug.get("chutes_em_gol_1")
    return by_slug.get(f"trader_back_{user_id}") or by_slug.get("trader_back_1")


def match_label(market: str) -> str:
    body = market
    if body.startswith("Football / "):
        body = body[len("Football / ") :]
    if " : " in body:
        fixture, sel = body.split(" : ", 1)
    else:
        fixture, sel = body, ""
    fixture = fixture.replace(" v ", " x ")
    label = f"{fixture} — {sel}" if sel else fixture
    return label[:160]


def pandl_id(market: str, settled: str, pl: float) -> str:
    raw = f"{settled}|{market}|{pl:.2f}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12]


def load_month(path: Path, year: int, month: int) -> list[dict]:
    rows: list[dict] = []
    with path.open(encoding="utf-8-sig", newline="") as f:
        for r in csv.DictReader(f):
            settled_at = parse_bf_dt(r["Settled date"])
            local = settled_at.replace(tzinfo=ZoneInfo("UTC")).astimezone(ZoneInfo(settings.app_timezone))
            if local.year != year or local.month != month:
                continue
            pl = parse_money(r["Profit/Loss (R$)"])
            key = market_key(r["Market"])
            pid = pandl_id(r["Market"], r["Settled date"], pl)
            rows.append(
                {
                    "market": r["Market"],
                    "key": key,
                    "pl": round(pl, 2),
                    "settled_at": settled_at,
                    "outcome": "WIN" if pl > 0 else ("LOSS" if pl < 0 else "WIN"),
                    "pid": pid,
                    "label": match_label(r["Market"]),
                }
            )
    return rows


def sync_month_summaries(db, user_id: int, year: int, month: int, comp_code: str) -> int:
    branches = db.query(Branch).filter(Branch.user_id == user_id).all()
    updated = 0
    for branch in branches:
        bets = [
            b
            for b in branch.bets
            if bet_in_period(b, year, month) and (b.competition_code or COMP_CODE) == comp_code
        ]
        summary = (
            db.query(BranchMonthlySummary)
            .filter_by(branch_id=branch.id, year=year, month=month, competition_code=comp_code)
            .one_or_none()
        )
        if not bets:
            if summary:
                db.delete(summary)
                updated += 1
            continue

        wins = sum(1 for b in bets if b.outcome == "WIN")
        losses = sum(1 for b in bets if b.outcome == "LOSS")
        pending = sum(1 for b in bets if b.outcome == "PENDING")
        total_pl = round(sum(b.profit_loss for b in bets), 2)
        if branch.side == "LAY":
            total_stake = round(sum(b.stake * max(b.odds - 1, 0) for b in bets), 2)
        else:
            total_stake = round(sum(b.stake for b in bets), 2)

        if summary:
            summary.bet_count = len(bets)
            summary.win_count = wins
            summary.loss_count = losses
            summary.pending_count = pending
            summary.total_pl = total_pl
            summary.total_stake = total_stake
            summary.commission_rate = branch.commission_rate
        else:
            db.add(
                BranchMonthlySummary(
                    branch_id=branch.id,
                    year=year,
                    month=month,
                    competition_code=comp_code,
                    bet_count=len(bets),
                    win_count=wins,
                    loss_count=losses,
                    pending_count=pending,
                    total_pl=total_pl,
                    total_stake=total_stake,
                    commission_rate=branch.commission_rate,
                    closed_at=datetime.utcnow(),
                )
            )
        updated += 1
    return updated


def clear_month_bets(db, user_id: int, year: int, month: int) -> int:
    """Remove TODAS as apostas do mês (refaz apontamento do zero)."""
    bets = db.query(Bet).join(Branch).filter(Branch.user_id == user_id).all()
    removed = 0
    for bet in bets:
        if bet_in_period(bet, year, month):
            db.delete(bet)
            removed += 1
    return removed


def import_month(
    db,
    path: Path,
    user: User,
    year: int,
    month: int,
    *,
    replace: bool,
    dry_run: bool,
) -> None:
    rows = load_month(path, year, month)
    branches = db.query(Branch).filter(Branch.user_id == user.id).all()
    by_slug = {b.slug: b for b in branches}

    to_import: list[dict] = []
    unmapped: list[dict] = []
    for row in rows:
        branch = resolve_branch(row["key"], by_slug, user.id)
        if not branch:
            unmapped.append(row)
            continue
        to_import.append({**row, "branch": branch})

    print(f"\n=== {month:02d}/{year} ===")
    print(f"CSV: {len(rows)} mercados | mapear: {len(to_import)} | sem filial: {len(unmapped)}")
    by_branch: dict[str, list] = defaultdict(list)
    for row in to_import:
        by_branch[row["branch"].name].append(row)
    for name, items in sorted(by_branch.items()):
        pl = sum(i["pl"] for i in items)
        print(f"  {name}: {len(items)} | R$ {pl:,.2f}")
    print(f"Total mês CSV: R$ {sum(r['pl'] for r in rows):,.2f}")

    if dry_run:
        return

    if replace:
        removed = clear_month_bets(db, user.id, year, month)
        print(f"Removidos lançamentos do mês: {removed}")
        db.flush()

    created = 0
    for row in to_import:
        pl = row["pl"]
        stake = abs(pl) if pl != 0 else 0.0
        desc = f"{row['label']} {PANDL_TAG}{row['pid']}]"
        db.add(
            Bet(
                branch_id=row["branch"].id,
                description=desc[:200],
                odds=2.0,
                stake=round(stake, 2),
                outcome=row["outcome"],
                profit_loss=pl,
                competition_code=COMP_CODE,
                created_at=row["settled_at"],
            )
        )
        created += 1

    db.flush()
    synced = sync_month_summaries(db, user.id, year, month, COMP_CODE)
    db.commit()

    bets = [
        b
        for b in db.query(Bet).join(Branch).filter(Branch.user_id == user.id).all()
        if bet_in_period(b, year, month)
    ]
    print(f"Importadas {created}. Consolidados: {synced}.")
    print(f"Ledger {month:02d}/{year}: {len(bets)} entradas, P&L R$ {sum(b.profit_loss for b in bets):,.2f}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "csv",
        nargs="?",
        default=r"c:\Users\Usuário\Downloads\BettingPandL (1).csv",
    )
    parser.add_argument("--year", type=int, default=2026)
    parser.add_argument(
        "--months",
        default="6,7",
        help="Meses a importar, ex: 6,7",
    )
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--replace",
        action="store_true",
        help="Apaga todas as entradas do mês antes de importar",
    )
    args = parser.parse_args()
    path = Path(args.csv)
    if not path.is_file():
        raise SystemExit(f"CSV not found: {path}")

    months = [int(x.strip()) for x in args.months.split(",") if x.strip()]
    db = SessionLocal()
    user = db.query(User).filter(User.email == USER_EMAIL).first()
    if not user:
        raise SystemExit(f"User not found: {USER_EMAIL}")

    print(f"CSV: {path.name} | meses={months} | replace={args.replace} | dry_run={args.dry_run}")
    for month in months:
        import_month(
            db,
            path,
            user,
            args.year,
            month,
            replace=args.replace,
            dry_run=args.dry_run,
        )
    db.close()


if __name__ == "__main__":
    main()

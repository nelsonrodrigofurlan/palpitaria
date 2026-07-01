"""Import Betfair settled CSV into Palpitaria branch bets (idempotent by Betfair bet id)."""

from __future__ import annotations

import argparse
import csv
import re
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from palpitaria.config import settings
from palpitaria.database import SessionLocal
from palpitaria.models import Bet, Branch, BranchMonthlySummary, User
from palpitaria.services.ledger import (
    bet_in_period,
    bet_local_period,
    betfair_csv_net_pl,
    is_betfair_imported_bet,
)

USER_EMAIL = "nelson.r.furlan@gmail.com"
COMP_CODE = "WC"
COMMISSION = 6.5

MONTHS_PT = {
    "jan": 1,
    "fev": 2,
    "mar": 3,
    "abr": 4,
    "mai": 5,
    "jun": 6,
    "jul": 7,
    "ago": 8,
    "set": 9,
    "out": 10,
    "nov": 11,
    "dez": 12,
}

BF_ID_RE = re.compile(r"(?:ID Aposta Betfair 1:|BF:)(\d+)")


def parse_money(raw: str) -> float | None:
    s = (raw or "").strip().replace(" ", "").replace(",", ".")
    if not s or s == "--":
        return None
    return float(s)


def norm_match(desc: str) -> str:
    m = re.match(
        r"^(.+?)\s+(Mais de|Menos de|0 - 0|[A-Za-zÀ-ÿ].*?-Resultado|Cabo Verde \+1)",
        desc,
    )
    if m:
        return m.group(1).strip()
    return desc.split("|")[0].strip()[:80]


def market_key(desc: str) -> str:
    d = desc.lower()
    if "mais de 0,5" in d or "mais de 0.5" in d:
        return "over_0_5"
    if "mais de 1,5" in d or "mais de 1.5" in d:
        return "over_1_5"
    if "mais de 2,5" in d or "mais de 2.5" in d:
        return "over_2_5"
    if "menos de 2,5" in d or "menos de 2.5" in d:
        return "under_2_5"
    if "menos de 4,5" in d or "menos de 4.5" in d:
        return "under_4_5"
    if "placar correto" in d or "0 - 0" in d:
        return "lay_cs"
    if "+1" in d or "-1" in d:
        return "ah"
    if "-resultado" in d:
        return "1x2"
    return "other"


def csv_outcome(status: str, pl: float | None) -> str:
    if status.lower().startswith("ganh"):
        return "WIN"
    if status.lower().startswith("perd"):
        return "LOSS"
    return "WIN" if pl and pl > 0 else "LOSS"


def extract_betfair_id(desc: str) -> str | None:
    m = BF_ID_RE.search(desc)
    return m.group(1) if m else None


def parse_betfair_dt(raw: str) -> datetime:
    parts = raw.strip().split()
    date_part = parts[0]
    time_part = parts[1] if len(parts) > 1 else "12:00:00"
    day_s, mon_s, yr_s = date_part.split("-")
    year = 2000 + int(yr_s)
    month = MONTHS_PT[mon_s.lower()]
    day = int(day_s)
    h, mi, sec = (int(x) for x in time_part.split(":"))
    local = datetime(year, month, day, h, mi, sec, tzinfo=ZoneInfo(settings.app_timezone))
    return local.astimezone(ZoneInfo("UTC")).replace(tzinfo=None)


def load_csv(path: Path) -> list[dict]:
    rows: list[dict] = []
    with path.open(encoding="utf-8-sig", newline="") as f:
        for r in csv.DictReader(f):
            pl = parse_money(r["Lucro/Perda"])
            stake = parse_money(r["Valor Apostado (R$)"])
            odds = parse_money(r["Cotações"])
            side = "LAY" if r["Tipo"].strip().lower() == "contra" else "BACK"
            desc = r["Descrição"]
            bf_id = extract_betfair_id(desc)
            rows.append(
                {
                    "placed": r["Realizada"],
                    "placed_at": parse_betfair_dt(r["Realizada"]),
                    "match": norm_match(desc),
                    "market": market_key(desc),
                    "side": side,
                    "odds": round(odds or 0, 2),
                    "stake": round(stake, 2) if stake is not None else 0.0,
                    "pl": round(pl, 2) if pl is not None else 0.0,
                    "outcome": csv_outcome(r["Status"], pl),
                    "bf_id": bf_id,
                    "raw": desc,
                }
            )
    return rows


def resolve_branch(row: dict, by_slug: dict[str, Branch], user_id: int) -> Branch | None:
    market = row["market"]
    side = row["side"]

    if market == "over_0_5" and side == "BACK":
        return by_slug.get("over_0_5")
    if market == "over_1_5" and side == "BACK":
        return by_slug.get("over_1_5")
    if market == "over_2_5" and side == "BACK":
        return by_slug.get("over_2.5_gols_1")
    if market == "1x2" and side == "BACK":
        return by_slug.get("1x2")
    if market == "lay_cs" and side == "LAY":
        return by_slug.get("correct_score_1")
    if market == "under_4_5" and side == "BACK":
        return by_slug.get("under_4,5_gols_1")
    if market == "ah" and side == "BACK":
        return by_slug.get(f"handicap_ah_{user_id}")
    if market == "under_2_5" and side == "BACK":
        return by_slug.get(f"trader_back_{user_id}")
    if side == "LAY":
        return by_slug.get(f"trader_lay_{user_id}")
    if market in ("under_2_5", "other") and side == "BACK":
        return by_slug.get(f"trader_back_{user_id}")
    return None


def net_pl_for_row(row: dict, branch: Branch) -> float:
    if row["outcome"] not in ("WIN", "LOSS"):
        return 0.0
    return round(
        betfair_csv_net_pl(
            row["stake"],
            row["odds"],
            row["outcome"],
            branch.commission_rate,
            side=branch.side,
        ),
        2,
    )


def existing_bf_ids(db, user_id: int) -> set[str]:
    ids: set[str] = set()
    bets = db.query(Bet).join(Branch).filter(Branch.user_id == user_id).all()
    for bet in bets:
        m = BF_ID_RE.search(bet.description)
        if m:
            ids.add(m.group(1))
    return ids


def sync_month_summaries(
    db,
    user_id: int,
    year: int,
    month: int,
    comp_code: str,
) -> int:
    """Atualiza consolidados mensais a partir das apostas (sem apagar entradas)."""
    branches = db.query(Branch).filter(Branch.user_id == user_id).all()
    updated = 0
    for branch in branches:
        bets = [
            b
            for b in branch.bets
            if bet_in_period(b, year, month)
            and (b.competition_code or COMP_CODE) == comp_code
        ]
        if not bets:
            continue

        wins = sum(1 for b in bets if b.outcome == "WIN")
        losses = sum(1 for b in bets if b.outcome == "LOSS")
        pending = sum(1 for b in bets if b.outcome == "PENDING")
        total_pl = round(sum(b.profit_loss for b in bets), 2)
        if branch.side == "LAY":
            total_stake = round(sum(b.stake * (b.odds - 1) for b in bets), 2)
        else:
            total_stake = round(sum(b.stake for b in bets), 2)

        summary = (
            db.query(BranchMonthlySummary)
            .filter_by(
                branch_id=branch.id,
                year=year,
                month=month,
                competition_code=comp_code,
            )
            .one_or_none()
        )
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


def import_csv(
    path: Path,
    *,
    year: int | None = None,
    month: int | None = None,
    dry_run: bool = False,
) -> None:
    if not path.is_file():
        raise SystemExit(f"CSV not found: {path}")

    rows = load_csv(path)
    db = SessionLocal()
    user = db.query(User).filter(User.email == USER_EMAIL).first()
    if not user:
        raise SystemExit(f"User not found: {USER_EMAIL}")

    branches = db.query(Branch).filter(Branch.user_id == user.id).all()
    by_slug = {b.slug: b for b in branches}
    known_bf = existing_bf_ids(db, user.id)

    to_import: list[dict] = []
    skipped_period = 0
    skipped_dup = 0
    unmapped: list[dict] = []

    for row in rows:
        placed_local = row["placed_at"].replace(tzinfo=ZoneInfo("UTC")).astimezone(
            ZoneInfo(settings.app_timezone)
        )
        py, pm = placed_local.year, placed_local.month
        if year is not None and month is not None and (py, pm) != (year, month):
            skipped_period += 1
            continue
        if row["bf_id"] and row["bf_id"] in known_bf:
            skipped_dup += 1
            continue

        branch = resolve_branch(row, by_slug, user.id)
        if not branch:
            unmapped.append(row)
            continue

        to_import.append({**row, "branch": branch, "period": (py, pm)})

    print(f"CSV: {path.name} — {len(rows)} linhas")
    print(f"  Importar: {len(to_import)} | Duplicadas: {skipped_dup} | Fora do período: {skipped_period}")
    if unmapped:
        print(f"  Sem filial: {len(unmapped)}")
        for row in unmapped[:5]:
            print(f"    - {row['match']} | {row['market']} {row['side']}")

    if dry_run:
        by_branch: dict[str, list] = {}
        for row in to_import:
            by_branch.setdefault(row["branch"].name, []).append(row)
        for name, items in sorted(by_branch.items()):
            pl = sum(net_pl_for_row(i, i["branch"]) for i in items)
            print(f"  {name}: {len(items)} entradas, P&L líquido R$ {pl:,.2f}")
        db.close()
        return

    created = 0
    periods: set[tuple[int, int]] = set()
    for row in to_import:
        branch = row["branch"]
        desc = f"{row['match']} [BF:{row['bf_id']}]" if row["bf_id"] else row["match"]
        bet = Bet(
            branch_id=branch.id,
            description=desc,
            odds=row["odds"],
            stake=row["stake"],
            outcome=row["outcome"],
            profit_loss=net_pl_for_row(row, branch),
            competition_code=COMP_CODE,
            created_at=row["placed_at"],
        )
        db.add(bet)
        if row["bf_id"]:
            known_bf.add(row["bf_id"])
        periods.add(row["period"])
        created += 1

    db.flush()

    synced = 0
    for py, pm in sorted(periods):
        synced += sync_month_summaries(db, user.id, py, pm, COMP_CODE)

    db.commit()

    bets = db.query(Bet).join(Branch).filter(Branch.user_id == user.id).all()
    total_pl = sum(b.profit_loss for b in bets)
    print(f"\nImportadas {created} entradas. Consolidados atualizados: {synced} filial-mês.")
    print(f"Ledger ativo: {len(bets)} entradas, P&L total R$ {total_pl:,.2f}")

    june = [b for b in bets if bet_in_period(b, 2026, 6)]
    print(f"Junho/2026: {len(june)} entradas, P&L R$ {sum(b.profit_loss for b in june):,.2f}")

    for br in sorted(branches, key=lambda b: b.name.lower()):
        bb = [b for b in june if b.branch_id == br.id]
        if bb:
            pl = sum(b.profit_loss for b in bb)
            print(f"  {br.name}: {len(bb)} | R$ {pl:,.2f}")

    db.close()


def recalc_betfair_bets(user_email: str = USER_EMAIL) -> None:
    """Recalcula P&L líquido das entradas [BF:...] com a comissão da filial."""
    db = SessionLocal()
    user = db.query(User).filter(User.email == user_email).first()
    if not user:
        raise SystemExit(f"User not found: {user_email}")

    bets = (
        db.query(Bet)
        .join(Branch)
        .filter(Branch.user_id == user.id)
        .all()
    )
    updated = 0
    periods: set[tuple[int, int]] = set()
    for bet in bets:
        if not is_betfair_imported_bet(bet):
            continue
        branch = bet.branch
        if bet.outcome not in ("WIN", "LOSS"):
            continue
        new_pl = round(
            betfair_csv_net_pl(
                bet.stake,
                bet.odds,
                bet.outcome,
                branch.commission_rate,
                side=branch.side,
            ),
            2,
        )
        if bet.profit_loss != new_pl:
            bet.profit_loss = new_pl
            updated += 1
        y, m = bet_local_period(bet.created_at)
        periods.add((y, m))

    synced = 0
    for py, pm in sorted(periods):
        synced += sync_month_summaries(db, user.id, py, pm, COMP_CODE)

    db.commit()
    all_bets = [b for b in bets if is_betfair_imported_bet(b)]
    total = round(sum(b.profit_loss for b in all_bets), 2)
    print(f"Recalculadas {updated} entradas Betfair. P&L líquido total: R$ {total:,.2f}")
    print(f"Consolidados atualizados: {synced} filial-mês.")
    db.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Import Betfair settled CSV into filiais")
    parser.add_argument("csv", nargs="?", default=r"c:\Users\Usuário\Downloads\ExchangeBets_Settled (1).csv")
    parser.add_argument("--year", type=int, default=2026)
    parser.add_argument("--month", type=int, default=6)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--recalc",
        action="store_true",
        help="Recalcula P&L líquido (comissão da filial) nas entradas [BF:...] já importadas",
    )
    args = parser.parse_args()

    if args.recalc:
        recalc_betfair_bets()
        return

    import_csv(Path(args.csv), year=args.year, month=args.month, dry_run=args.dry_run)


if __name__ == "__main__":
    main()

"""Fechamento mensal das filiais — consolida e zera o ledger ativo."""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from palpitaria.config import settings
from palpitaria.models import Bet, Branch, BranchMonthlySummary

MONTHS_PT = (
    "",
    "Janeiro",
    "Fevereiro",
    "Março",
    "Abril",
    "Maio",
    "Junho",
    "Julho",
    "Agosto",
    "Setembro",
    "Outubro",
    "Novembro",
    "Dezembro",
)


def current_period() -> tuple[int, int]:
    now = datetime.now(ZoneInfo(settings.app_timezone))
    return now.year, now.month


def bet_local_period(created_at: datetime) -> tuple[int, int]:
    utc = created_at.replace(tzinfo=ZoneInfo("UTC"))
    local = utc.astimezone(ZoneInfo(settings.app_timezone))
    return local.year, local.month


def period_label(year: int, month: int) -> str:
    name = MONTHS_PT[month] if 1 <= month <= 12 else str(month)
    return f"{name}/{year}"


def close_past_months(db: Session) -> list[BranchMonthlySummary]:
    """
    Arquiva entradas de meses anteriores (por filial e competição) e remove do ledger ativo.
    O mês corrente permanece nos cards de Filiais.
    """
    cy, cm = current_period()
    bets = db.query(Bet).all()
    if not bets:
        return []

    # Agrupar por (ano, mês, branch_id, competition_code)
    groups: dict[tuple[int, int, int, str], list[Bet]] = defaultdict(list)
    for bet in bets:
        y, m = bet_local_period(bet.created_at)
        if (y, m) < (cy, cm):
            comp = bet.competition_code or "WC"
            groups[(y, m, bet.branch_id, comp)].append(bet)

    if not groups:
        return []

    created: list[BranchMonthlySummary] = []
    for (year, month, branch_id, comp_code), branch_bets in sorted(groups.items()):
        existing = (
            db.query(BranchMonthlySummary)
            .filter_by(branch_id=branch_id, year=year, month=month, competition_code=comp_code)
            .one_or_none()
        )
        if existing:
            for bet in branch_bets:
                db.delete(bet)
            continue

        branch = db.query(Branch).filter_by(id=branch_id).one_or_none()
        wins = sum(1 for b in branch_bets if b.outcome == "WIN")
        losses = sum(1 for b in branch_bets if b.outcome == "LOSS")
        pending = sum(1 for b in branch_bets if b.outcome == "PENDING")
        total_pl = round(sum(b.profit_loss for b in branch_bets), 2)
        total_stake = round(sum(b.stake for b in branch_bets), 2)

        summary = BranchMonthlySummary(
            branch_id=branch_id,
            year=year,
            month=month,
            competition_code=comp_code,
            bet_count=len(branch_bets),
            win_count=wins,
            loss_count=losses,
            pending_count=pending,
            total_pl=total_pl,
            total_stake=total_stake,
            commission_rate=branch.commission_rate if branch else 6.5,
            closed_at=datetime.utcnow(),
        )
        db.add(summary)
        created.append(summary)
        for bet in branch_bets:
            db.delete(bet)

    if created or groups:
        db.commit()
    return created

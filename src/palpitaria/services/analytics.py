"""Agregações para dashboard de gráficos — P&L real e performance IA."""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from typing import Any

from sqlalchemy import func
from sqlalchemy.orm import Session

from palpitaria.config import settings
from palpitaria.models import AiRecommendation, Bet, Branch, BranchMonthlySummary, User
from palpitaria.services.ai_tracker import analysis_local_date, normalize_market_group
from palpitaria.services.ledger import bet_local_period, current_period, period_label


def _round2(value: float) -> float:
    return round(float(value), 2)


def _hit_rate_pct(hits: int, total: int) -> int | None:
    if total <= 0:
        return None
    return round(hits / total * 100)


def _latest_ia_per_fixture(recommendations: list[AiRecommendation]) -> list[AiRecommendation]:
    latest: dict[int, AiRecommendation] = {}
    for rec in sorted(recommendations, key=lambda r: r.analyzed_at):
        latest[rec.fixture_id] = rec
    return list(latest.values())


def _user_bets(db: Session, user_id: int, comp_code: str | None = None) -> list[Bet]:
    query = (
        db.query(Bet)
        .join(Branch, Bet.branch_id == Branch.id)
        .filter(Branch.user_id == user_id)
        .order_by(Bet.created_at)
    )
    if comp_code:
        query = query.filter(func.coalesce(Bet.competition_code, settings.world_cup_code) == comp_code)
    return query.all()


def _user_summaries(db: Session, user_id: int, comp_code: str | None = None) -> list[BranchMonthlySummary]:
    query = (
        db.query(BranchMonthlySummary)
        .join(Branch, BranchMonthlySummary.branch_id == Branch.id)
        .filter(Branch.user_id == user_id)
    )
    if comp_code:
        query = query.filter(BranchMonthlySummary.competition_code == comp_code)
    return query.all()


def _monthly_pl_from_summaries(summaries: list[BranchMonthlySummary]) -> dict[tuple[int, int], float]:
    buckets: dict[tuple[int, int], float] = defaultdict(float)
    for summary in summaries:
        buckets[(summary.year, summary.month)] += summary.total_pl
    return buckets


def build_pl_charts(db: Session, user_id: int, *, comp_code: str | None = None) -> dict[str, Any]:
    bets = _user_bets(db, user_id, comp_code)
    summaries = _user_summaries(db, user_id, comp_code)
    branches = (
        db.query(Branch)
        .filter(Branch.user_id == user_id)
        .order_by(Branch.name)
        .all()
    )
    branch_by_id = {b.id: b for b in branches}

    resolved = [b for b in bets if b.outcome in ("WIN", "LOSS")]
    pending = [b for b in bets if b.outcome == "PENDING"]

    # --- Curva de equity (P&L cumulativo por aposta) ---
    equity_labels: list[str] = []
    equity_values: list[float] = []
    running = 0.0
    for bet in resolved:
        running += bet.profit_loss
        dt = bet.created_at
        equity_labels.append(dt.strftime("%d/%m") if dt else "—")
        equity_values.append(_round2(running))

    # --- P&L diário ---
    daily: dict[str, float] = defaultdict(float)
    for bet in resolved:
        if not bet.created_at:
            continue
        key = bet.created_at.strftime("%Y-%m-%d")
        daily[key] += bet.profit_loss
    daily_sorted = sorted(daily.items())
    daily_labels = [datetime.strptime(k, "%Y-%m-%d").strftime("%d/%m") for k, _ in daily_sorted]
    daily_values = [_round2(v) for _, v in daily_sorted]

    # --- P&L mensal (summaries + bets do mês corrente) ---
    cy, cm = current_period()
    monthly: dict[tuple[int, int], float] = _monthly_pl_from_summaries(summaries)
    for bet in resolved:
        y, m = bet_local_period(bet.created_at)
        if (y, m) == (cy, cm):
            monthly[(y, m)] += bet.profit_loss
    month_keys = sorted(monthly.keys())
    monthly_labels = [period_label(y, m) for y, m in month_keys]
    monthly_values = [_round2(monthly[k]) for k in month_keys]

    # --- Por filial ---
    branch_stats: dict[int, dict[str, float | int]] = defaultdict(
        lambda: {"pl": 0.0, "wins": 0, "losses": 0, "pending": 0, "stake": 0.0}
    )
    for bet in bets:
        bucket = branch_stats[bet.branch_id]
        bucket["pl"] += bet.profit_loss
        if bet.outcome == "WIN":
            bucket["wins"] += 1
        elif bet.outcome == "LOSS":
            bucket["losses"] += 1
        else:
            bucket["pending"] += 1
        branch = branch_by_id.get(bet.branch_id)
        if branch and branch.side == "LAY":
            bucket["stake"] += bet.stake * (bet.odds - 1)
        else:
            bucket["stake"] += bet.stake

    for summary in summaries:
        bucket = branch_stats[summary.branch_id]
        bucket["pl"] += summary.total_pl
        bucket["wins"] += summary.win_count
        bucket["losses"] += summary.loss_count
        bucket["pending"] += summary.pending_count
        bucket["stake"] += summary.total_stake

    branch_rows = []
    for branch in branches:
        stats = branch_stats.get(branch.id)
        if not stats or (stats["wins"] + stats["losses"] + stats["pending"]) == 0:
            continue
        closed = stats["wins"] + stats["losses"]
        branch_rows.append(
            {
                "name": branch.name,
                "pl": _round2(stats["pl"]),
                "wins": stats["wins"],
                "losses": stats["losses"],
                "pending": stats["pending"],
                "stake": _round2(stats["stake"]),
                "hit_rate": _hit_rate_pct(stats["wins"], closed),
            }
        )
    branch_rows.sort(key=lambda r: r["name"].lower())

    def _is_goals_branch(name: str) -> bool:
        n = name.lower()
        return "over" in n or "under" in n

    goals_rows = [r for r in branch_rows if _is_goals_branch(r["name"])]
    alt_rows = [r for r in branch_rows if not _is_goals_branch(r["name"])]

    def _real_hit_rate(rows: list[dict]) -> int:
        wins = sum(r["wins"] for r in rows)
        closed = sum(r["wins"] + r["losses"] for r in rows)
        return _hit_rate_pct(wins, closed) or 0

    real_by_scope = {
        "labels": ["Homologadas (Gols)", "Alternativas (1X2/CS)"],
        "hit_rate": [_real_hit_rate(goals_rows), _real_hit_rate(alt_rows)],
        "pl": [_round2(sum(r["pl"] for r in goals_rows)), _round2(sum(r["pl"] for r in alt_rows))],
    }

    total_pl = _round2(sum(r["pl"] for r in branch_rows))
    total_wins = sum(r["wins"] for r in branch_rows)
    total_losses = sum(r["losses"] for r in branch_rows)
    total_stake = _round2(sum(r["stake"] for r in branch_rows))

    user = db.get(User, user_id)
    deposits = user.total_deposits if user else 0.0
    withdrawals = user.total_withdrawals if user else 0.0
    bankroll = _round2(deposits - withdrawals + total_pl)

    # --- Evolução da banca (depósito + P&L cumulativo) ---
    bankroll_labels = ["Início"] + equity_labels if equity_labels else ["Início"]
    bankroll_values = [_round2(deposits - withdrawals)]
    running_pl = 0.0
    for bet in resolved:
        running_pl += bet.profit_loss
        bankroll_values.append(_round2(deposits - withdrawals + running_pl))

    return {
        "kpis": {
            "total_pl": total_pl,
            "bankroll": bankroll,
            "total_bets": total_wins + total_losses + sum(r["pending"] for r in branch_rows),
            "hit_rate": _hit_rate_pct(total_wins, total_wins + total_losses),
            "total_stake": total_stake,
            "wins": total_wins,
            "losses": total_losses,
            "pending": len(pending),
        },
        "equity_curve": {"labels": equity_labels, "values": equity_values},
        "daily_pl": {"labels": daily_labels, "values": daily_values},
        "monthly_pl": {"labels": monthly_labels, "values": monthly_values},
        "by_branch": {
            "labels": [r["name"] for r in branch_rows],
            "pl": [r["pl"] for r in branch_rows],
            "wins": [r["wins"] for r in branch_rows],
            "losses": [r["losses"] for r in branch_rows],
            "hit_rate": [r["hit_rate"] or 0 for r in branch_rows],
            "stake": [r["stake"] for r in branch_rows],
        },
        "outcomes": {
            "wins": total_wins,
            "losses": total_losses,
            "pending": sum(r["pending"] for r in branch_rows),
        },
        "bankroll": {"labels": bankroll_labels, "values": bankroll_values},
        "real_by_scope": real_by_scope,
    }


def build_ia_charts(db: Session, *, comp_code: str | None = None) -> dict[str, Any]:
    query = db.query(AiRecommendation).order_by(AiRecommendation.analyzed_at)
    if comp_code:
        query = query.filter(AiRecommendation.competition_code == comp_code)
    all_recs = _latest_ia_per_fixture(query.all())

    resolved = [r for r in all_recs if r.outcome in ("HIT", "MISS")]
    pending = [r for r in all_recs if r.outcome == "PENDING"]
    hits = [r for r in resolved if r.outcome == "HIT"]

    hom = [r for r in all_recs if not r.excluded]
    alt = [r for r in all_recs if r.excluded]

    def scope_stats(pool: list[AiRecommendation]) -> dict[str, int]:
        res = [r for r in pool if r.outcome in ("HIT", "MISS")]
        return {
            "hits": sum(1 for r in res if r.outcome == "HIT"),
            "misses": sum(1 for r in res if r.outcome == "MISS"),
            "pending": sum(1 for r in pool if r.outcome == "PENDING"),
            "total_resolved": len(res),
        }

    hom_s = scope_stats(hom)
    alt_s = scope_stats(alt)

    # --- Por mês ---
    monthly_buckets: dict[tuple[int, int], dict[str, int]] = defaultdict(
        lambda: {"hits": 0, "misses": 0, "pending": 0, "total": 0}
    )
    for rec in all_recs:
        d = analysis_local_date(rec.analyzed_at)
        key = (d.year, d.month)
        monthly_buckets[key]["total"] += 1
        if rec.outcome == "HIT":
            monthly_buckets[key]["hits"] += 1
        elif rec.outcome == "MISS":
            monthly_buckets[key]["misses"] += 1
        else:
            monthly_buckets[key]["pending"] += 1

    month_keys = sorted(monthly_buckets.keys())
    ia_monthly_labels = [period_label(y, m) for y, m in month_keys]
    ia_monthly_hits = [monthly_buckets[k]["hits"] for k in month_keys]
    ia_monthly_misses = [monthly_buckets[k]["misses"] for k in month_keys]
    ia_monthly_hit_rate = [
        _hit_rate_pct(monthly_buckets[k]["hits"], monthly_buckets[k]["hits"] + monthly_buckets[k]["misses"]) or 0
        for k in month_keys
    ]

    # --- Curva cumulativa de acerto IA ---
    cum_labels: list[str] = []
    cum_values: list[float] = []
    sorted_resolved = sorted(resolved, key=lambda r: r.analyzed_at)
    h = 0
    for i, rec in enumerate(sorted_resolved, start=1):
        if rec.outcome == "HIT":
            h += 1
        cum_labels.append(analysis_local_date(rec.analyzed_at).strftime("%d/%m"))
        cum_values.append(_round2(h / i * 100))

    # --- Por mercado ---
    market_buckets: dict[str, dict[str, int]] = defaultdict(lambda: {"hits": 0, "misses": 0})
    for rec in resolved:
        group = normalize_market_group(rec.market)
        if rec.outcome == "HIT":
            market_buckets[group]["hits"] += 1
        else:
            market_buckets[group]["misses"] += 1

    market_rows = sorted(
        [
            {
                "market": k,
                "hits": v["hits"],
                "misses": v["misses"],
                "hit_rate": _hit_rate_pct(v["hits"], v["hits"] + v["misses"]) or 0,
            }
            for k, v in market_buckets.items()
        ],
        key=lambda r: -(r["hits"] + r["misses"]),
    )

    return {
        "kpis": {
            "total_picks": len(all_recs),
            "resolved": len(resolved),
            "hit_rate": _hit_rate_pct(len(hits), len(resolved)),
            "homologated_hit_rate": _hit_rate_pct(hom_s["hits"], hom_s["total_resolved"]),
            "alternate_hit_rate": _hit_rate_pct(alt_s["hits"], alt_s["total_resolved"]),
            "pending": len(pending),
        },
        "scope": {
            "labels": ["Homologadas", "Alternativas"],
            "hits": [hom_s["hits"], alt_s["hits"]],
            "misses": [hom_s["misses"], alt_s["misses"]],
            "pending": [hom_s["pending"], alt_s["pending"]],
            "hit_rate": [
                _hit_rate_pct(hom_s["hits"], hom_s["total_resolved"]) or 0,
                _hit_rate_pct(alt_s["hits"], alt_s["total_resolved"]) or 0,
            ],
        },
        "monthly": {
            "labels": ia_monthly_labels,
            "hits": ia_monthly_hits,
            "misses": ia_monthly_misses,
            "hit_rate": ia_monthly_hit_rate,
        },
        "cumulative_accuracy": {"labels": cum_labels, "values": cum_values},
        "by_market": {
            "labels": [r["market"] for r in market_rows],
            "hits": [r["hits"] for r in market_rows],
            "misses": [r["misses"] for r in market_rows],
            "hit_rate": [r["hit_rate"] for r in market_rows],
        },
        "outcomes": {
            "hits": len(hits),
            "misses": len(resolved) - len(hits),
            "pending": len(pending),
        },
    }


def build_dashboard_payload(
    db: Session,
    user_id: int,
    *,
    comp_code: str | None = None,
) -> dict[str, Any]:
    pl = build_pl_charts(db, user_id, comp_code=comp_code)
    ia = build_ia_charts(db, comp_code=comp_code)
    cy, cm = current_period()
    return {
        "pl": pl,
        "ia": ia,
        "meta": {
            "comp_code": comp_code or "ALL",
            "current_period": period_label(cy, cm),
            "generated_at": datetime.utcnow().isoformat(),
        },
    }

"""Registro e resolução automática das recomendações da IA."""

from __future__ import annotations

import json
import unicodedata
from datetime import date, datetime
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session, joinedload

from palpitaria.config import settings
from palpitaria.models import AiRecommendation, Fixture, FixtureReport
from palpitaria.services.analyzer import FixtureAnalysis


def _norm_name(value: str) -> str:
    text = unicodedata.normalize("NFKD", value or "")
    text = "".join(c for c in text if not unicodedata.combining(c))
    return text.lower().strip()


def analysis_local_date(dt: datetime) -> date:
    utc = dt.replace(tzinfo=ZoneInfo("UTC"))
    return utc.astimezone(ZoneInfo(settings.app_timezone)).date()


def normalize_market(market: str) -> str:
    return (market or "").upper().replace(",", ".").strip()


def _team_won(picked: str, home_name: str, away_name: str, home_score: int, away_score: int) -> bool:
    picked_n = _norm_name(picked)
    home_n = _norm_name(home_name)
    away_n = _norm_name(away_name)
    if picked_n in home_n or home_n in picked_n:
        return home_score > away_score
    if picked_n in away_n or away_n in picked_n:
        return away_score > home_score
    return False


def evaluate_market(
    market: str,
    *,
    home_name: str,
    away_name: str,
    home_score: int,
    away_score: int,
) -> str:
    """Retorna HIT, MISS ou VOID para o mercado recomendado."""
    m = normalize_market(market)
    total = home_score + away_score

    if "OVER 0.5" in m:
        return "HIT" if total >= 1 else "MISS"
    if "OVER 1.5" in m:
        return "HIT" if total >= 2 else "MISS"
    if "OVER 2.5" in m:
        return "HIT" if total >= 3 else "MISS"

    if m.startswith("VITÓRIA:") or m.startswith("VITORIA:"):
        team = market.split(":", 1)[1].strip()
        return "HIT" if _team_won(team, home_name, away_name, home_score, away_score) else "MISS"

    if "LAY CORRECT SCORE: 0-0" in m or "LAY 0-0" in m:
        return "HIT" if total > 0 else "MISS"
    if "LAY CORRECT SCORE: 1-0" in m:
        return "MISS" if home_score == 1 and away_score == 0 else "HIT"
    if "LAY CORRECT SCORE: 2-0" in m:
        return "MISS" if home_score == 2 and away_score == 0 else "HIT"

    return "VOID"


def record_ai_recommendation(
    db: Session,
    analysis: FixtureAnalysis,
    *,
    competition_code: str,
) -> AiRecommendation | None:
    """Grava snapshot da recomendação (uma por jogo por dia; re-run no mesmo dia atualiza)."""
    pick = analysis.best_pick
    if not pick or not pick.get("market"):
        return None

    now = datetime.utcnow()
    today = analysis_local_date(now)

    existing = (
        db.query(AiRecommendation)
        .filter(AiRecommendation.fixture_id == analysis.fixture_id)
        .order_by(AiRecommendation.analyzed_at.desc())
        .all()
    )
    rec = next((r for r in existing if analysis_local_date(r.analyzed_at) == today), None)

    payload = {
        "competition_code": competition_code,
        "match_label": f"{analysis.home_name} x {analysis.away_name}",
        "analyzed_at": now,
        "market": pick.get("market", ""),
        "verdict": pick.get("verdict", "CANDIDATE"),
        "reason": pick.get("reason"),
        "scope": pick.get("scope", "alternate" if analysis.excluded else "goals"),
        "excluded": analysis.excluded,
        "goal_potential_score": analysis.goal_potential_score,
        "outcome": "PENDING",
        "final_home_score": None,
        "final_away_score": None,
        "resolved_at": None,
    }

    if rec:
        for key, value in payload.items():
            setattr(rec, key, value)
    else:
        rec = AiRecommendation(fixture_id=analysis.fixture_id, **payload)
        db.add(rec)

    db.flush()
    return rec


def resolve_pending_recommendations(db: Session, competition_code: str | None = None) -> int:
    """Resolve recomendações pendentes quando o jogo já terminou com placar."""
    query = (
        db.query(AiRecommendation)
        .options(joinedload(AiRecommendation.fixture).joinedload(Fixture.home_team))
        .options(joinedload(AiRecommendation.fixture).joinedload(Fixture.away_team))
        .filter(AiRecommendation.outcome == "PENDING")
    )
    if competition_code:
        query = query.filter(AiRecommendation.competition_code == competition_code)

    resolved = 0
    for rec in query.all():
        fixture = rec.fixture
        if not fixture or fixture.status != "FINISHED":
            continue
        if fixture.home_score is None or fixture.away_score is None:
            continue

        home_name = fixture.home_team.name if fixture.home_team else "?"
        away_name = fixture.away_team.name if fixture.away_team else "?"

        outcome = evaluate_market(
            rec.market,
            home_name=home_name,
            away_name=away_name,
            home_score=fixture.home_score,
            away_score=fixture.away_score,
        )
        rec.outcome = outcome
        rec.final_home_score = fixture.home_score
        rec.final_away_score = fixture.away_score
        rec.resolved_at = datetime.utcnow()
        resolved += 1

    if resolved:
        db.commit()
    return resolved


def backfill_from_fixture_reports(db: Session) -> int:
    """Importa recomendações já salvas em fixture_reports (uma vez)."""
    reports = (
        db.query(FixtureReport)
        .filter(FixtureReport.best_pick_json.isnot(None))
        .all()
    )
    created = 0
    for report in reports:
        if db.query(AiRecommendation).filter_by(fixture_id=report.fixture_id).count():
            continue
        try:
            pick = json.loads(report.best_pick_json or "{}")
        except json.JSONDecodeError:
            continue
        if not pick.get("market"):
            continue

        fixture = (
            db.query(Fixture)
            .options(joinedload(Fixture.home_team), joinedload(Fixture.away_team))
            .filter_by(id=report.fixture_id)
            .one_or_none()
        )
        if not fixture or not fixture.home_team or not fixture.away_team:
            continue

        rec = AiRecommendation(
            fixture_id=fixture.id,
            competition_code=fixture.competition_code,
            match_label=f"{fixture.home_team.name} x {fixture.away_team.name}",
            analyzed_at=report.analyzed_at,
            market=pick.get("market", ""),
            verdict=pick.get("verdict", "CANDIDATE"),
            reason=pick.get("reason"),
            scope=pick.get("scope", "goals"),
            excluded=report.excluded,
            goal_potential_score=report.goal_potential_score,
            outcome="PENDING",
        )
        db.add(rec)
        created += 1

    if created:
        db.commit()
    resolve_pending_recommendations(db)
    return created


def compute_accuracy_stats(recommendations: list[AiRecommendation]) -> dict:
    """Métricas sobre lista já filtrada (usa última por fixture para evitar dupla contagem)."""
    latest_by_fixture: dict[int, AiRecommendation] = {}
    for rec in sorted(recommendations, key=lambda r: r.analyzed_at):
        latest_by_fixture[rec.fixture_id] = rec

    rows = list(latest_by_fixture.values())
    resolved = [r for r in rows if r.outcome in ("HIT", "MISS")]
    hits = [r for r in resolved if r.outcome == "HIT"]
    pending = [r for r in rows if r.outcome == "PENDING"]

    by_market: dict[str, dict] = {}
    for rec in resolved:
        bucket = by_market.setdefault(rec.market, {"hit": 0, "miss": 0, "total": 0})
        bucket["total"] += 1
        if rec.outcome == "HIT":
            bucket["hit"] += 1
        else:
            bucket["miss"] += 1

    return {
        "total": len(rows),
        "resolved": len(resolved),
        "hits": len(hits),
        "misses": len(resolved) - len(hits),
        "pending": len(pending),
        "hit_rate_pct": round(len(hits) / len(resolved) * 100) if resolved else None,
        "by_market": by_market,
    }


def _latest_per_fixture(recommendations: list[AiRecommendation]) -> list[AiRecommendation]:
    latest: dict[int, AiRecommendation] = {}
    for rec in sorted(recommendations, key=lambda r: r.analyzed_at):
        latest[rec.fixture_id] = rec
    return sorted(latest.values(), key=lambda r: r.analyzed_at, reverse=True)


def _row_from_rec(rec: AiRecommendation) -> dict:
    score = "—"
    if rec.final_home_score is not None and rec.final_away_score is not None:
        score = f"{rec.final_home_score} x {rec.final_away_score}"
    return {
        "match_label": rec.match_label,
        "market": rec.market,
        "verdict": rec.verdict,
        "excluded": rec.excluded,
        "analyzed_at": rec.analyzed_at,
        "outcome": rec.outcome,
        "score": score,
    }


def group_recommendations_by_month(recommendations: list[AiRecommendation]) -> list[dict]:
    """Agrupa por mês (timezone app) com métricas e linhas compactas."""
    from collections import defaultdict

    from palpitaria.services.ledger import period_label

    buckets: dict[tuple[int, int], list[AiRecommendation]] = defaultdict(list)
    for rec in recommendations:
        d = analysis_local_date(rec.analyzed_at)
        buckets[(d.year, d.month)].append(rec)

    months: list[dict] = []
    for year, month in sorted(buckets.keys(), reverse=True):
        recs = buckets[(year, month)]
        deduped = _latest_per_fixture(recs)
        stats = compute_accuracy_stats(recs)
        rows = [_row_from_rec(r) for r in deduped]
        months.append(
            {
                "period": period_label(year, month),
                "year": year,
                "month": month,
                "stats": stats,
                "rows": rows,
            }
        )
    return months


def monthly_summary_rows(recommendations: list[AiRecommendation]) -> list[dict]:
    """Uma linha por mês para tabela analítica no topo."""
    return [
        {
            "period": block["period"],
            "total": block["stats"]["total"],
            "resolved": block["stats"]["resolved"],
            "hits": block["stats"]["hits"],
            "misses": block["stats"]["misses"],
            "pending": block["stats"]["pending"],
            "hit_rate_pct": block["stats"]["hit_rate_pct"],
        }
        for block in group_recommendations_by_month(recommendations)
    ]

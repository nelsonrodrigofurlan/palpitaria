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


def normalize_market_group(market: str) -> str:
    """Agrupa mercados para estatística — ex.: VITÓRIA: Portugal → VITÓRIA."""
    m = normalize_market(market)
    if m.startswith("VITÓRIA:") or m.startswith("VITORIA:"):
        return "VITÓRIA"
    if m.startswith("LAY CORRECT SCORE:"):
        return "LAY CORRECT SCORE"
    return market.strip()


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
    now = datetime.utcnow()
    today = analysis_local_date(now)

    existing = (
        db.query(AiRecommendation)
        .filter(AiRecommendation.fixture_id == analysis.fixture_id)
        .order_by(AiRecommendation.analyzed_at.desc())
        .all()
    )
    rec = next((r for r in existing if analysis_local_date(r.analyzed_at) == today), None)

    if not pick or not pick.get("market"):
        if rec:
            fixture = db.get(Fixture, analysis.fixture_id)
            if fixture and fixture.status == "FINISHED":
                return rec
            db.delete(rec)
            db.flush()
        return None

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
    }

    if rec:
        fixture = db.get(Fixture, analysis.fixture_id)
        if fixture and fixture.status == "FINISHED":
            return rec
        for key, value in payload.items():
            setattr(rec, key, value)
        if rec.outcome not in ("HIT", "MISS"):
            rec.outcome = "PENDING"
            rec.final_home_score = None
            rec.final_away_score = None
            rec.resolved_at = None
    else:
        rec = AiRecommendation(
            fixture_id=analysis.fixture_id,
            outcome="PENDING",
            final_home_score=None,
            final_away_score=None,
            resolved_at=None,
            **payload,
        )
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


def compute_accuracy_stats(
    recommendations: list[AiRecommendation],
    *,
    homologated_only: bool | None = None,
) -> dict:
    """Métricas com última leitura por jogo. homologated_only: True=candidatos, False=alternativas, None=todos."""
    if homologated_only is True:
        pool = [r for r in recommendations if not r.excluded]
    elif homologated_only is False:
        pool = [r for r in recommendations if r.excluded]
    else:
        pool = list(recommendations)

    latest_by_fixture: dict[int, AiRecommendation] = {}
    for rec in sorted(pool, key=lambda r: r.analyzed_at):
        latest_by_fixture[rec.fixture_id] = rec

    rows = list(latest_by_fixture.values())
    resolved = [r for r in rows if r.outcome in ("HIT", "MISS")]
    hits = [r for r in resolved if r.outcome == "HIT"]
    pending = [r for r in rows if r.outcome == "PENDING"]

    by_market: dict[str, dict] = {}
    for rec in resolved:
        group = normalize_market_group(rec.market)
        bucket = by_market.setdefault(group, {"hit": 0, "miss": 0, "total": 0})
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


def compute_split_stats(recommendations: list[AiRecommendation]) -> dict:
    """Separa métricas: homologadas (candidatos) vs alternativas — snapshot gravado."""
    return {
        "homologated": compute_accuracy_stats(recommendations, homologated_only=True),
        "alternate": compute_accuracy_stats(recommendations, homologated_only=False),
    }


def ensure_ia_history_from_reports(
    db: Session,
    comp_code: str,
    year: int,
    month: int,
) -> int:
    """Garante registro no histórico a partir do snapshot da análise (fixture_reports).

    Só adiciona jogos finalizados — pendentes seguem a lógica live (podem ser descartados).
    """
    reports = (
        db.query(FixtureReport)
        .join(Fixture, FixtureReport.fixture_id == Fixture.id)
        .filter(Fixture.competition_code == comp_code)
        .filter(FixtureReport.best_pick_json.isnot(None))
        .all()
    )
    touched = 0
    for report in reports:
        if not report.analyzed_at:
            continue
        d = analysis_local_date(report.analyzed_at)
        if d.year != year or d.month != month:
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

        if fixture.status != "FINISHED":
            continue

        home_name = fixture.home_team.name
        away_name = fixture.away_team.name
        outcome = _outcome_for_fixture(fixture, pick["market"], home_name, away_name)
        rec = db.query(AiRecommendation).filter_by(fixture_id=report.fixture_id).first()

        if rec:
            if fixture.status == "FINISHED" and rec.market != pick["market"]:
                rec.market = pick["market"]
                rec.verdict = pick.get("verdict", rec.verdict)
                rec.reason = pick.get("reason")
                rec.scope = pick.get("scope", rec.scope)
                rec.excluded = report.excluded
                rec.goal_potential_score = report.goal_potential_score
                rec.outcome = outcome
                rec.final_home_score = fixture.home_score if outcome in ("HIT", "MISS") else None
                rec.final_away_score = fixture.away_score if outcome in ("HIT", "MISS") else None
                rec.resolved_at = datetime.utcnow() if outcome in ("HIT", "MISS") else None
                touched += 1
            continue

        rec = AiRecommendation(
            fixture_id=fixture.id,
            competition_code=comp_code,
            match_label=f"{home_name} x {away_name}",
            analyzed_at=report.analyzed_at,
            market=pick.get("market", ""),
            verdict=pick.get("verdict", "CANDIDATE"),
            reason=pick.get("reason"),
            scope=pick.get("scope", "alternate" if report.excluded else "goals"),
            excluded=report.excluded,
            goal_potential_score=report.goal_potential_score,
            outcome=outcome,
            final_home_score=fixture.home_score if outcome in ("HIT", "MISS") else None,
            final_away_score=fixture.away_score if outcome in ("HIT", "MISS") else None,
            resolved_at=datetime.utcnow() if outcome in ("HIT", "MISS") else None,
        )
        db.add(rec)
        touched += 1

    if touched:
        db.commit()
    return touched


def prune_discarded_pending_recommendations(db: Session, comp_code: str) -> int:
    """Remove recomendações pendentes de jogos ainda não finalizados que a lógica atual descartaria."""
    from palpitaria.services.analyzer import analyze_fixture

    pending = (
        db.query(AiRecommendation)
        .join(Fixture, AiRecommendation.fixture_id == Fixture.id)
        .filter(AiRecommendation.competition_code == comp_code)
        .filter(AiRecommendation.outcome == "PENDING")
        .filter(Fixture.status != "FINISHED")
        .all()
    )
    removed = 0
    for rec in pending:
        fixture = rec.fixture or db.get(Fixture, rec.fixture_id)
        if not fixture:
            db.delete(rec)
            removed += 1
            continue
        analysis = analyze_fixture(db, fixture)
        if analysis.best_pick and analysis.best_pick.get("market"):
            continue
        db.delete(rec)
        removed += 1

    if removed:
        db.commit()
    return removed


def filter_recommendations_by_month(
    recommendations: list[AiRecommendation],
    year: int,
    month: int,
) -> list[AiRecommendation]:
    return [
        r
        for r in recommendations
        if analysis_local_date(r.analyzed_at).year == year
        and analysis_local_date(r.analyzed_at).month == month
    ]


def parse_month_param(mes: str | None) -> tuple[int, int]:
    from palpitaria.services.ledger import current_period

    cy, cm = current_period()
    if not mes:
        return cy, cm
    try:
        parts = mes.strip().split("-")
        if len(parts) != 2:
            return cy, cm
        return int(parts[0]), int(parts[1])
    except (ValueError, TypeError):
        return cy, cm


def build_month_options(recommendations: list[AiRecommendation]) -> list[dict]:
    from palpitaria.services.ledger import current_period, period_label

    cy, cm = current_period()
    keys: set[tuple[int, int]] = {(cy, cm)}
    for rec in recommendations:
        d = analysis_local_date(rec.analyzed_at)
        keys.add((d.year, d.month))

    options = []
    for year, month in sorted(keys, reverse=True):
        options.append(
            {
                "value": f"{year}-{month:02d}",
                "label": period_label(year, month),
            }
        )
    return options


def market_rows_from_stats(stats: dict) -> list[dict]:
    rows = []
    for market, data in sorted(stats["by_market"].items(), key=lambda x: -x[1]["total"]):
        rows.append(
            {
                "market": market,
                "hit": data["hit"],
                "miss": data["miss"],
                "total": data["total"],
                "hit_rate_pct": round(data["hit"] / data["total"] * 100) if data["total"] else None,
            }
        )
    return rows


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


def _outcome_for_fixture(fixture: Fixture, market: str, home_name: str, away_name: str) -> str:
    if fixture.status != "FINISHED" or fixture.home_score is None or fixture.away_score is None:
        return "PENDING"
    return evaluate_market(
        market,
        home_name=home_name,
        away_name=away_name,
        home_score=fixture.home_score,
        away_score=fixture.away_score,
    )


def rows_for_scope(recommendations: list[AiRecommendation], *, homologated: bool) -> list[dict]:
    """Lista entradas do snapshot gravado na análise — histórico imutável."""
    pool = [r for r in recommendations if r.excluded != homologated]
    return [_row_from_rec(r) for r in _latest_per_fixture(pool)]

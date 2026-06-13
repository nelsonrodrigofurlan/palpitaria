from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session, joinedload

from palpitaria.config import settings
from palpitaria.models import Fixture
from palpitaria.services.ingest import latest_profile


@dataclass
class CriterionResult:
    name: str
    value: float | str | int
    threshold: str
    passed: bool
    detail: str


@dataclass
class FixtureAnalysis:
    fixture_id: int
    external_id: int
    home_name: str
    away_name: str
    home_crest: str | None
    away_crest: str | None
    utc_date: datetime
    status: str
    stage: str | None
    group_name: str | None
    goal_potential_score: float
    excluded: bool
    exclusion_reasons: list[str] = field(default_factory=list)
    criteria: list[CriterionResult] = field(default_factory=list)
    best_pick: dict | None = None  # Substitui a lista de picks por uma única recomendação
    llm_explanation: str | None = None
    home_insights: dict | None = None
    away_insights: dict | None = None
    match_context: dict | None = None  # Clima, Árbitro, Gramado


@dataclass
class TodayContext:
    date_local: date
    label: str
    timezone: str
    start_utc: datetime
    end_utc: datetime


def get_today_context(tz_name: str | None = None) -> TodayContext:
    tz = ZoneInfo(tz_name or settings.app_timezone)
    now_local = datetime.now(tz)
    start_local = datetime.combine(now_local.date(), time.min, tzinfo=tz)
    end_local = start_local + timedelta(days=1)
    start_utc = start_local.astimezone(ZoneInfo("UTC")).replace(tzinfo=None)
    end_utc = end_local.astimezone(ZoneInfo("UTC")).replace(tzinfo=None)
    return TodayContext(
        date_local=now_local.date(),
        label=now_local.strftime("%d/%m/%Y"),
        timezone=tz.key,
        start_utc=start_utc,
        end_utc=end_utc,
    )


def _scheduled_fixtures_query(db: Session):
    return (
        db.query(Fixture)
        .options(joinedload(Fixture.home_team), joinedload(Fixture.away_team))
        .filter(Fixture.competition_code == settings.world_cup_code)
        .filter(Fixture.status.in_(["SCHEDULED", "TIMED", "IN_PLAY"]))
    )


def count_today_fixtures(db: Session, tz_name: str | None = None) -> int:
    ctx = get_today_context(tz_name)
    return (
        _scheduled_fixtures_query(db)
        .filter(Fixture.utc_date >= ctx.start_utc)
        .filter(Fixture.utc_date < ctx.end_utc)
        .count()
    )


def count_upcoming_fixtures(db: Session) -> int:
    return _scheduled_fixtures_query(db).count()


def analyze_fixture(db: Session, fixture: Fixture) -> FixtureAnalysis:
    home_profile = latest_profile(db, fixture.home_team_id)
    away_profile = latest_profile(db, fixture.away_team_id)

    home = fixture.home_team
    away = fixture.away_team
    analysis = FixtureAnalysis(
        fixture_id=fixture.id,
        external_id=fixture.external_id,
        home_name=home.name,
        away_name=away.name,
        home_crest=home.crest_url,
        away_crest=away.crest_url,
        utc_date=fixture.utc_date,
        status=fixture.status,
        stage=fixture.stage,
        group_name=fixture.group_name,
        goal_potential_score=0.0,
        excluded=False,
    )

    if home_profile is None or away_profile is None:
        analysis.excluded = True
        analysis.exclusion_reasons.append("Perfil estatístico incompleto — ingestão necessária")
        return analysis

    if home_profile.matches_sampled < 1 or away_profile.matches_sampled < 1:
        analysis.excluded = True
        analysis.exclusion_reasons.append(
            f"Amostra insuficiente ({home_profile.matches_sampled}/{away_profile.matches_sampled} jogos)"
        )
        return analysis

    combined_avg = (
        home_profile.avg_goals_scored
        + home_profile.avg_goals_conceded
        + away_profile.avg_goals_scored
        + away_profile.avg_goals_conceded
    ) / 2
    min_over_05 = min(home_profile.over_05_rate, away_profile.over_05_rate)
    max_zero_zero = max(home_profile.zero_zero_rate, away_profile.zero_zero_rate)
    avg_btts = (home_profile.both_teams_score_rate + away_profile.both_teams_score_rate) / 2

    criteria = [
        CriterionResult(
            name="combined_avg_goals",
            value=round(combined_avg, 3),
            threshold=f">= {settings.min_combined_avg_goals}",
            passed=combined_avg >= settings.min_combined_avg_goals,
            detail="Média combinada de gols marcados e sofridos",
        ),
        CriterionResult(
            name="max_zero_zero_rate",
            value=round(max_zero_zero, 3),
            threshold=f"<= {settings.max_zero_zero_rate}",
            passed=max_zero_zero <= settings.max_zero_zero_rate,
            detail="Pior taxa de 0-0 entre as duas seleções",
        ),
        CriterionResult(
            name="min_over_05_rate",
            value=round(min_over_05, 3),
            threshold=f">= {settings.min_over_05_historical_rate}",
            passed=min_over_05 >= settings.min_over_05_historical_rate,
            detail="Menor taxa histórica de over 0,5",
        ),
        CriterionResult(
            name="both_teams_score_rate",
            value=round(avg_btts, 3),
            threshold=f">= {settings.min_both_score_rate}",
            passed=avg_btts >= settings.min_both_score_rate,
            detail="Média de jogos em que ambas marcam",
        ),
        CriterionResult(
            name="home_offense",
            value=round(home_profile.avg_goals_scored, 3),
            threshold=">= 0.8",
            passed=home_profile.avg_goals_scored >= 0.8,
            detail=f"{home.name} — gols marcados/jogo",
        ),
        CriterionResult(
            name="away_offense",
            value=round(away_profile.avg_goals_scored, 3),
            threshold=">= 0.8",
            passed=away_profile.avg_goals_scored >= 0.8,
            detail=f"{away.name} — gols marcados/jogo",
        ),
    ]
    analysis.criteria = criteria

    failed = [c for c in criteria if not c.passed]
    if failed:
        analysis.excluded = True
        analysis.exclusion_reasons = [f"{c.name}: {c.value} ({c.detail})" for c in failed]

    passed_weight = sum(1 for c in criteria if c.passed)
    analysis.goal_potential_score = round((passed_weight / len(criteria)) * 100, 1)

    if not analysis.excluded:
        analysis.best_pick = _select_best_pick(analysis, home_profile, away_profile, combined_avg, min_over_05)

    # Attach insights if available
    if home_profile.insights_json:
        analysis.home_insights = json.loads(home_profile.insights_json)
    if away_profile.insights_json:
        analysis.away_insights = json.loads(away_profile.insights_json)

    return analysis


def default_match_context() -> dict:
    return {
        "weather": "Aguardando coleta (clima no horário do jogo)",
        "referee": "Aguardando coleta (árbitro e estilo)",
        "pitch": "Aguardando coleta (estado do gramado)",
    }


def _select_best_pick(
    analysis: FixtureAnalysis,
    home_profile,
    away_profile,
    combined_avg: float,
    min_over_05: float,
) -> dict:
    confidence = analysis.goal_potential_score
    over_15_rate = min(home_profile.over_15_rate, away_profile.over_15_rate)
    over_25_rate = min(home_profile.over_25_rate, away_profile.over_25_rate)
    home_win_rate = home_profile.win_rate
    away_win_rate = away_profile.win_rate

    # Lógica de decisão para a ÚNICA melhor recomendação
    
    # 1. Prioridade Máxima: Chuva de Gols (Over 2.5)
    if combined_avg >= 3.2 and over_25_rate >= 0.55 and confidence >= 95:
        return {
            "market": "OVER 2.5 GOALS",
            "verdict": "STRONG",
            "reason": f"Média altíssima ({combined_avg:.1f}) e histórico de Over 2.5 em {over_25_rate:.0%}. Cenário de jogo muito aberto."
        }

    # 2. Dominância Clara (1X2)
    if abs(home_win_rate - away_win_rate) >= 0.30 and confidence >= 90:
        fav_name = analysis.home_name if home_win_rate > away_win_rate else analysis.away_name
        fav_rate = max(home_win_rate, away_win_rate)
        return {
            "market": f"VITÓRIA: {fav_name}",
            "verdict": "STRONG" if fav_rate >= 0.75 else "CANDIDATE",
            "reason": f"Superioridade técnica clara. {fav_name} venceu {fav_rate:.0%} dos jogos recentes."
        }

    # 3. Segurança no Over 1.5
    if over_15_rate >= 0.72 and combined_avg >= 2.4 and confidence >= 90:
        return {
            "market": "OVER 1.5 GOALS",
            "verdict": "STRONG",
            "reason": f"Histórico sólido de pelo menos 2 gols ({over_15_rate:.0%}) com média combinada de {combined_avg:.1f}."
        }

    # 4. Base de Segurança: Over 0.5 (Anti-Zero-Gols)
    return {
        "market": "OVER 0.5 GOALS",
        "verdict": "STRONG" if confidence >= 85 else "CANDIDATE",
        "reason": f"Filtro anti-zero-gols aprovado com Score {confidence}. Média combinada de {combined_avg:.1f} gols/jogo."
    }


def analyze_upcoming(
    db: Session,
    limit: int = 50,
    *,
    for_today_only: bool = True,
    tz_name: str | None = None,
) -> list[FixtureAnalysis]:
    query = _scheduled_fixtures_query(db)
    if for_today_only:
        ctx = get_today_context(tz_name)
        query = query.filter(Fixture.utc_date >= ctx.start_utc).filter(Fixture.utc_date < ctx.end_utc)
    fixtures = query.order_by(Fixture.utc_date).limit(limit).all()
    return [analyze_fixture(db, fixture) for fixture in fixtures]


def persist_analysis(db: Session, analysis: FixtureAnalysis, llm_explanation: str | None = None) -> None:
    from palpitaria.models import FixtureReport

    explanation = llm_explanation or analysis.llm_explanation
    report = db.query(FixtureReport).filter_by(fixture_id=analysis.fixture_id).one_or_none()
    if report is None:
        report = FixtureReport(fixture_id=analysis.fixture_id)
        db.add(report)

    report.excluded = analysis.excluded
    report.exclusion_reasons_json = json.dumps(analysis.exclusion_reasons, ensure_ascii=False)
    report.criteria_json = json.dumps([asdict(c) for c in analysis.criteria], ensure_ascii=False)
    report.goal_potential_score = analysis.goal_potential_score
    report.llm_explanation = explanation
    report.best_pick_json = json.dumps(analysis.best_pick, ensure_ascii=False) if analysis.best_pick else None
    report.match_context_json = (
        json.dumps(analysis.match_context, ensure_ascii=False) if analysis.match_context else None
    )
    report.analyzed_at = datetime.utcnow()

    db.commit()


def attach_saved_reports(db: Session, analyses: list[FixtureAnalysis]) -> None:
    from palpitaria.models import FixtureReport

    if not analyses:
        return
    fixture_ids = [a.fixture_id for a in analyses]
    reports = db.query(FixtureReport).filter(FixtureReport.fixture_id.in_(fixture_ids)).all()
    by_fixture = {r.fixture_id: r for r in reports}
    for analysis in analyses:
        report = by_fixture.get(analysis.fixture_id)
        if not report:
            continue
        if report.llm_explanation:
            analysis.llm_explanation = report.llm_explanation
        if report.best_pick_json:
            analysis.best_pick = json.loads(report.best_pick_json)
        if report.match_context_json:
            analysis.match_context = json.loads(report.match_context_json)
        elif not analysis.excluded and report.llm_explanation:
            analysis.match_context = default_match_context()


def count_teams_with_profiles(db: Session) -> tuple[int, int]:
    from palpitaria.models import Team

    total = db.query(Team).count()
    ready = 0
    for team in db.query(Team).all():
        profile = latest_profile(db, team.id)
        if profile and profile.matches_sampled >= 1:
            ready += 1
    return ready, total

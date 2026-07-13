from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, time, timedelta
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session, joinedload

from palpitaria.config import settings
from palpitaria.models import Fixture
from palpitaria.services.ingest import latest_profile
from palpitaria.services.team_names import localize_team_name
from palpitaria.services.chat_service import get_valid_insights_for_team
from palpitaria.services.competitions import get_competition_profile
from palpitaria.services.foundation import both_profiles_solid
from palpitaria.services.knockout_climate import (
    adjust_best_pick_for_knockout,
    enrich_analysis_knockout,
    is_knockout_stage,
    knockout_over25_thresholds,
)
from palpitaria.services.match_context_utils import default_match_context
from palpitaria.services.prediction import predict_match


@dataclass
class CriterionResult:
    name: str
    value: float | str | int
    threshold: str
    passed: bool
    detail: str
    level: str = "fail"  # fail | ok | strong


def _criterion_level(
    value: float,
    *,
    passed: bool,
    higher_is_better: bool,
    strong_at: float,
) -> str:
    if not passed:
        return "fail"
    if higher_is_better:
        return "strong" if value >= strong_at else "ok"
    return "strong" if value <= strong_at else "ok"


def _make_criterion(
    name: str,
    value: float,
    threshold: str,
    passed: bool,
    detail: str,
    *,
    higher_is_better: bool,
    strong_at: float,
) -> CriterionResult:
    return CriterionResult(
        name=name,
        value=round(value, 3),
        threshold=threshold,
        passed=passed,
        detail=detail,
        level=_criterion_level(
            value, passed=passed, higher_is_better=higher_is_better, strong_at=strong_at
        ),
    )


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
    home_stats_meta: dict | None = None  # Perfil híbrido (API + web) — mandante
    away_stats_meta: dict | None = None  # Perfil híbrido (API + web) — visitante
    criteria_brief: dict | None = None  # Resumo gerencial dos achados numéricos
    strategy_card: dict | None = None  # 2–3 estratégias estruturadas (exchange)
    is_knockout: bool = False  # Fase eliminatória (mata-mata)
    prediction: dict | None = None  # Saída do motor Poisson (probs + λ)
    venue_stadium: str | None = None
    venue_city: str | None = None
    venue_state: str | None = None

    @property
    def venue_label(self) -> str | None:
        if self.venue_city and self.venue_state:
            return f"{self.venue_city}, {self.venue_state}"
        return self.venue_city or self.venue_state

    @property
    def strong_criteria_count(self) -> int:
        return sum(1 for c in self.criteria if c.level == "strong")


@dataclass
class TodayContext:
    date_local: date
    label: str
    timezone: str
    start_utc: datetime
    end_utc: datetime


def operational_local_date(dt: datetime, tz_name: str | None = None) -> date:
    """Data operacional de um instante (dia começa às app_day_start_hour no fuso do app)."""
    tz = ZoneInfo(tz_name or settings.app_timezone)
    if dt.tzinfo is None:
        local = dt.replace(tzinfo=ZoneInfo("UTC")).astimezone(tz)
    else:
        local = dt.astimezone(tz)
    if local.hour < settings.app_day_start_hour:
        return local.date() - timedelta(days=1)
    return local.date()


def get_today_context(tz_name: str | None = None, *, now: datetime | None = None) -> TodayContext:
    tz = ZoneInfo(tz_name or settings.app_timezone)
    now_local = now.astimezone(tz) if now is not None else datetime.now(tz)
    op_date = operational_local_date(now_local, tz_name)
    start_local = datetime.combine(
        op_date, time(settings.app_day_start_hour, 0), tzinfo=tz
    )
    end_local = start_local + timedelta(days=1)
    start_utc = start_local.astimezone(ZoneInfo("UTC")).replace(tzinfo=None)
    end_utc = end_local.astimezone(ZoneInfo("UTC")).replace(tzinfo=None)
    return TodayContext(
        date_local=op_date,
        label=op_date.strftime("%d/%m/%Y"),
        timezone=tz.key,
        start_utc=start_utc,
        end_utc=end_utc,
    )


def _scheduled_fixtures_query(db: Session, competition_code: str | None = None):
    query = (
        db.query(Fixture)
        .options(joinedload(Fixture.home_team), joinedload(Fixture.away_team))
        .filter(Fixture.status.in_(["SCHEDULED", "TIMED", "IN_PLAY"]))
    )
    if competition_code:
        query = query.filter(Fixture.competition_code == competition_code)
    else:
        query = query.filter(Fixture.competition_code == settings.world_cup_code)
    return query


def count_today_fixtures(db: Session, tz_name: str | None = None, competition_code: str | None = None) -> int:
    ctx = get_today_context(tz_name)
    return (
        _scheduled_fixtures_query(db, competition_code=competition_code)
        .filter(Fixture.utc_date >= ctx.start_utc)
        .filter(Fixture.utc_date < ctx.end_utc)
        .count()
    )


def count_upcoming_fixtures(db: Session, competition_code: str | None = None) -> int:
    return _scheduled_fixtures_query(db, competition_code=competition_code).count()


def analyze_fixture(db: Session, fixture: Fixture) -> FixtureAnalysis:
    home_profile = latest_profile(db, fixture.home_team_id)
    away_profile = latest_profile(db, fixture.away_team_id)

    home = fixture.home_team
    away = fixture.away_team
    comp_code = fixture.competition_code or settings.world_cup_code

    analysis = FixtureAnalysis(
        fixture_id=fixture.id,
        external_id=fixture.external_id,
        home_name=localize_team_name(home.name, home.external_id),
        away_name=localize_team_name(away.name, away.external_id),
        home_crest=home.crest_url,
        away_crest=away.crest_url,
        utc_date=fixture.utc_date,
        status=fixture.status,
        stage=fixture.stage,
        group_name=fixture.group_name,
        goal_potential_score=0.0,
        excluded=False,
        venue_stadium=fixture.venue_stadium,
        venue_city=fixture.venue_city,
        venue_state=fixture.venue_state,
    )
    analysis.is_knockout = is_knockout_stage(fixture.stage)

    if home_profile is None or away_profile is None:
        analysis.excluded = True
        analysis.exclusion_reasons.append(
            "Perfil estatístico incompleto — rode passo 3 (Gerar Leituras) para coletar histórico via web"
        )
        _attach_criteria_brief(analysis)
        return analysis

    comp_profile = get_competition_profile(comp_code)
    min_games = comp_profile.min_sample_games
    if home_profile.matches_sampled < min_games or away_profile.matches_sampled < min_games:
        analysis.excluded = True
        analysis.exclusion_reasons.append(
            f"Base de dados reduzida ({home_profile.matches_sampled} vs {away_profile.matches_sampled} jogos). "
            f"Para este campeonato, o produto exige pelo menos {min_games} jogo(s) por seleção."
        )
        _attach_criteria_brief(analysis, home_profile, away_profile)
        return analysis

    # Portão de responsabilidade: sem histórico real = SEM palpite (nem alternativo)
    solid, solid_reasons = both_profiles_solid(
        home_profile, away_profile, min_matches=min_games
    )
    if not solid:
        analysis.excluded = True
        analysis.exclusion_reasons = [
            "Sem fundamento para palpite público — base histórica insuficiente ou provisória."
        ] + solid_reasons
        analysis.best_pick = None
        analysis.goal_potential_score = 0.0
        analysis.prediction = {
            "blocked": True,
            "reason": "foundation_gate",
            "details": solid_reasons,
        }
        analysis.home_stats_meta = _profile_stats_meta(home_profile)
        analysis.away_stats_meta = _profile_stats_meta(away_profile)
        analysis.home_stats_meta["user_insights"] = get_valid_insights_for_team(
            db, fixture.home_team_id
        )
        analysis.away_stats_meta["user_insights"] = get_valid_insights_for_team(
            db, fixture.away_team_id
        )
        if home_profile.insights_json:
            analysis.home_insights = json.loads(home_profile.insights_json)
        if away_profile.insights_json:
            analysis.away_insights = json.loads(away_profile.insights_json)
        _attach_criteria_brief(analysis, home_profile, away_profile)
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
        _make_criterion(
            "combined_avg_goals",
            combined_avg,
            f">= {settings.min_combined_avg_goals} gols/jogo",
            combined_avg >= settings.min_combined_avg_goals,
            "Média combinada de gols marcados e sofridos",
            higher_is_better=True,
            strong_at=settings.strong_combined_avg_goals,
        ),
        _make_criterion(
            "max_zero_zero_rate",
            max_zero_zero,
            f"<= {settings.max_zero_zero_rate} ({settings.max_zero_zero_rate:.0%})",
            max_zero_zero <= settings.max_zero_zero_rate,
            "Pior taxa de 0-0 entre as duas seleções",
            higher_is_better=False,
            strong_at=settings.strong_max_zero_zero_rate,
        ),
        _make_criterion(
            "min_over_05_rate",
            min_over_05,
            f">= {settings.min_over_05_historical_rate} ({settings.min_over_05_historical_rate:.0%})",
            min_over_05 >= settings.min_over_05_historical_rate,
            "Menor taxa histórica de over 0,5",
            higher_is_better=True,
            strong_at=settings.strong_over_05_historical_rate,
        ),
        _make_criterion(
            "both_teams_score_rate",
            avg_btts,
            f">= {settings.min_both_score_rate} ({settings.min_both_score_rate:.0%})",
            avg_btts >= settings.min_both_score_rate,
            "Média de jogos em que ambas marcam",
            higher_is_better=True,
            strong_at=settings.strong_both_score_rate,
        ),
        _make_criterion(
            "home_offense",
            home_profile.avg_goals_scored,
            f">= {settings.min_offense_goals} gols/jogo",
            home_profile.avg_goals_scored >= settings.min_offense_goals,
            f"{localize_team_name(home.name, home.external_id)} — gols marcados/jogo",
            higher_is_better=True,
            strong_at=settings.strong_offense_goals,
        ),
        _make_criterion(
            "away_offense",
            away_profile.avg_goals_scored,
            f">= {settings.min_offense_goals} gols/jogo",
            away_profile.avg_goals_scored >= settings.min_offense_goals,
            f"{localize_team_name(away.name, away.external_id)} — gols marcados/jogo",
            higher_is_better=True,
            strong_at=settings.strong_offense_goals,
        ),
    ]
    analysis.criteria = criteria

    failed = [c for c in criteria if not c.passed]
    if failed:
        analysis.excluded = True
        analysis.exclusion_reasons = [f"{c.name}: {c.value} ({c.detail})" for c in failed]

    passed_weight = sum(1 for c in criteria if c.passed)
    analysis.goal_potential_score = round((passed_weight / len(criteria)) * 100, 1)

    # Motor de predição (Poisson) — decide mercado; LLM só narra depois
    pred = predict_match(
        home_scored=home_profile.avg_goals_scored,
        home_conceded=home_profile.avg_goals_conceded,
        away_scored=away_profile.avg_goals_scored,
        away_conceded=away_profile.avg_goals_conceded,
        competition_code=comp_code,
        stage=analysis.stage,
        home_name=analysis.home_name,
        away_name=analysis.away_name,
    )
    analysis.prediction = pred.to_dict()
    analysis.is_knockout = pred.is_knockout or analysis.is_knockout

    model_pick = pred.as_best_pick()
    if analysis.excluded:
        # Fora do filtro de gols: só aceita alternativa do modelo (1X2) ou fallback legado
        if model_pick and model_pick.get("scope") == "alternate":
            analysis.best_pick = model_pick
        else:
            analysis.best_pick = _select_alternate_pick(
                analysis,
                home_profile,
                away_profile,
                combined_avg,
                max_zero_zero,
                avg_btts,
            )
    elif model_pick and model_pick.get("scope") == "goals":
        analysis.best_pick = model_pick
    elif model_pick and model_pick.get("scope") == "alternate":
        # Modelo sem Over sólido → não homologa gols; marca excluído leve
        analysis.best_pick = model_pick
        analysis.excluded = True
        analysis.exclusion_reasons.append(pred.reason)
    else:
        analysis.best_pick = None
        analysis.excluded = True
        analysis.exclusion_reasons.append(pred.reason or "Modelo: descarte total")

    analysis.best_pick = adjust_best_pick_for_knockout(
        analysis.best_pick,
        stage=analysis.stage,
    )

    analysis.home_stats_meta = _profile_stats_meta(home_profile)
    analysis.away_stats_meta = _profile_stats_meta(away_profile)

    # Injetar percepções validadas do usuário
    analysis.home_stats_meta["user_insights"] = get_valid_insights_for_team(db, fixture.home_team_id)
    analysis.away_stats_meta["user_insights"] = get_valid_insights_for_team(db, fixture.away_team_id)

    if home_profile.insights_json:
        analysis.home_insights = json.loads(home_profile.insights_json)
    if away_profile.insights_json:
        analysis.away_insights = json.loads(away_profile.insights_json)

    enrich_analysis_knockout(analysis)
    _attach_criteria_brief(analysis, home_profile, away_profile)
    return analysis


def _profile_stats_meta(profile) -> dict:
    """Snapshot of numeric profile + web/API provenance for LLM decisions."""
    meta = {
        "matches_sampled": profile.matches_sampled,
        "avg_goals_scored": profile.avg_goals_scored,
        "avg_goals_conceded": profile.avg_goals_conceded,
        "zero_zero_rate": profile.zero_zero_rate,
        "over_05_rate": profile.over_05_rate,
        "over_15_rate": profile.over_15_rate,
        "over_25_rate": profile.over_25_rate,
        "win_rate": profile.win_rate,
        "both_teams_score_rate": profile.both_teams_score_rate,
    }
    if profile.raw_json:
        try:
            raw = json.loads(profile.raw_json)
            meta.update({
                k: v
                for k, v in raw.items()
                if k not in meta
                or k in (
                    "source",
                    "api_matches",
                    "web_matches",
                    "confidence",
                    "sources_summary",
                    "recent_matches",
                    "calc_matches",
                )
            })
        except json.JSONDecodeError:
            pass
    return meta


def _fmt_num(value: float) -> str:
    rounded = round(float(value), 1)
    if rounded == int(rounded):
        return str(int(rounded))
    return f"{rounded:.1f}"


def _fmt_pct(value: float) -> str:
    return f"{float(value):.0%}"


def _profile_source_label(meta: dict) -> str:
    n = int(meta.get("matches_sampled") or 0)
    src = meta.get("source") or "api"
    kind = "web" if src == "web_research" else "API"
    games = "jogo" if n == 1 else "jogos"
    return f"{n} {games} ({kind})"


def _team_form_block(meta: dict, team_label: str) -> dict:
    recent = meta.get("recent_matches") or []
    calc = meta.get("calc_matches") or []
    sampled = int(meta.get("matches_sampled") or 0)

    if not recent and not calc:
        return {
            "team": team_label,
            "recent": ["Sem jogos salvos — rode «Gerar Leituras»."],
            "calc_note": None,
        }

    # Se temos poucos jogos na base de cálculo, usamos eles direto (mais preciso)
    # Caso contrário, usamos os 3 mais recentes.
    if calc and len(calc) <= 5:
        display_matches = calc
        calc_note = "Jogos usados no cálculo:"
    else:
        display_matches = recent[:3]
        calc_note = f"Média baseada em {sampled} jogos." if sampled > 3 else None

    lines = [m.get("line") or m.get("result", "—") for m in display_matches]
    return {"team": team_label, "recent": lines, "calc_note": calc_note}


def build_criteria_brief(analysis: FixtureAnalysis) -> dict:
    """Resumo curto e gerencial: de onde vieram os valores da tabela de critérios."""
    home = analysis.home_stats_meta
    away = analysis.away_stats_meta
    match = f"{analysis.home_name} x {analysis.away_name}"

    if not home or not away:
        return {
            "match": match,
            "base": "Base estatística indisponível.",
            "lines": ["Rode «Gerar Leituras» para montar perfis API + web das seleções."],
            "verdict": "Sem achados para cruzar com os limiares.",
        }

    hn, an = analysis.home_name, analysis.away_name
    combined = (
        float(home["avg_goals_scored"])
        + float(home["avg_goals_conceded"])
        + float(away["avg_goals_scored"])
        + float(away["avg_goals_conceded"])
    ) / 2
    worst_00 = max(float(home["zero_zero_rate"]), float(away["zero_zero_rate"]))
    min_o05 = min(float(home["over_05_rate"]), float(away["over_05_rate"]))
    avg_btts = (float(home["both_teams_score_rate"]) + float(away["both_teams_score_rate"])) / 2

    lines = [
        f"Média de gols: {_fmt_num(combined)} por jogo (expectativa combinada).",
        f"Risco de 0-0: {_fmt_pct(worst_00)} (baseado no pior histórico entre os dois).",
        f"Chance de gol (Over 0.5): {_fmt_pct(min_o05)} (frequência mínima garantida).",
        f"Ambas marcam: {_fmt_pct(avg_btts)} dos jogos (tendência mútua).",
    ]

    total = len(analysis.criteria)
    passed = sum(1 for c in analysis.criteria if c.passed)
    strong = analysis.strong_criteria_count
    failed = [c.detail for c in analysis.criteria if not c.passed]

    if not total:
        verdict = "Critérios ainda não calculados."
    elif failed:
        verdict = f"Resultado: {passed}/{total} aprovados ({strong} acima da média)."
    else:
        verdict = f"Resultado: {passed}/{total} aprovados — leitura sólida para Over."

    home_insights = (analysis.home_insights or {}).get("key_insights") or []
    away_insights = (analysis.away_insights or {}).get("key_insights") or []

    return {
        "match": match,
        "base": f"Fontes: {_profile_source_label(home)} | {_profile_source_label(away)}",
        "lines": lines,
        "verdict": verdict,
        "home_form": _team_form_block(home, hn),
        "away_form": _team_form_block(away, an),
        "home_highlights": home_insights[:3],
        "away_highlights": away_insights[:3],
    }


def _attach_criteria_brief(
    analysis: FixtureAnalysis,
    home_profile=None,
    away_profile=None,
) -> None:
    if home_profile is not None:
        analysis.home_stats_meta = _profile_stats_meta(home_profile)
    if away_profile is not None:
        analysis.away_stats_meta = _profile_stats_meta(away_profile)
    analysis.criteria_brief = build_criteria_brief(analysis)


def profile_from_meta(meta: dict | None):
    """Lightweight profile stand-in for infer_favorite when only meta dict exists."""
    if not meta:
        return None
    return SimpleNamespace(
        matches_sampled=int(meta.get("matches_sampled") or 0),
        avg_goals_scored=float(meta.get("avg_goals_scored") or 0),
        avg_goals_conceded=float(meta.get("avg_goals_conceded") or 0),
        win_rate=float(meta.get("win_rate") or 0),
        zero_zero_rate=float(meta.get("zero_zero_rate") or 0),
        over_05_rate=float(meta.get("over_05_rate") or 0),
        over_15_rate=float(meta.get("over_15_rate") or 0),
        over_25_rate=float(meta.get("over_25_rate") or 0),
        both_teams_score_rate=float(meta.get("both_teams_score_rate") or 0),
    )


@dataclass
class FavoriteRead:
    name: str
    strength: float
    basis: str
    detail: str


def _shrunk_mean(value: float, samples: int, *, prior: float, pseudo: int = 4) -> float:
    if samples <= 0:
        return prior
    return (value * samples + prior * pseudo) / (samples + pseudo)


def _decision_stats(profile) -> tuple[float, float, float, int, bool]:
    """Capped stats for pick logic — 1-game outliers (ex.: 7-0 errado) não dominam."""
    n = profile.matches_sampled
    scored = profile.avg_goals_scored
    conceded = profile.avg_goals_conceded
    win = profile.win_rate
    outlier_cap = False
    if n <= 1 and scored > 3.0:
        scored = 1.5
        outlier_cap = True
    if n <= 1 and win >= 0.99:
        win = 0.45
        outlier_cap = True
    elif n == 2 and scored > 4.0:
        scored = min(scored, 2.5)
        outlier_cap = True
    elif n == 2 and win >= 0.99:
        win = 0.55
        outlier_cap = True
    return scored, conceded, win, n, outlier_cap


def infer_favorite(
    analysis: FixtureAnalysis,
    home_profile,
    away_profile,
) -> FavoriteRead | None:
    h_scored, h_conceded, h_win, h_n, h_outlier = _decision_stats(home_profile)
    a_scored, a_conceded, a_win, a_n, a_outlier = _decision_stats(away_profile)

    h_attack = _shrunk_mean(h_scored, h_n, prior=1.2)
    a_attack = _shrunk_mean(a_scored, a_n, prior=1.2)
    h_def = _shrunk_mean(h_conceded, h_n, prior=1.0)
    a_def = _shrunk_mean(a_conceded, a_n, prior=1.0)

    home_power = h_attack + max(a_def - 1.0, 0) * 0.45 + 0.12
    away_power = a_attack + max(h_def - 1.0, 0) * 0.45
    matchup_edge = home_power - away_power

    win_rate_edge = 0.0
    win_detail = ""
    if min(h_n, a_n) >= settings.min_sample_for_win_rate_favorite:
        h_win_s = _shrunk_mean(h_win, h_n, prior=0.33)
        a_win_s = _shrunk_mean(a_win, a_n, prior=0.33)
        win_rate_edge = h_win_s - a_win_s
        win_detail = f"vitórias ajustadas {h_win_s:.0%} x {a_win_s:.0%}"

    win_weight = 0.4 if min(h_n, a_n) >= settings.min_sample_for_win_rate_favorite else 0.0
    edge = matchup_edge * (1 - win_weight) + win_rate_edge * win_weight

    threshold = 0.28
    if edge >= threshold:
        return FavoriteRead(
            name=analysis.home_name,
            strength=min(edge / 1.2, 1.0),
            basis="combined" if win_weight else "matchup",
            detail=win_detail or f"força ajustada {home_power:.2f} vs {away_power:.2f}",
        )
    if edge <= -threshold and not a_outlier:
        return FavoriteRead(
            name=analysis.away_name,
            strength=min(abs(edge) / 1.2, 1.0),
            basis="combined" if win_weight else "matchup",
            detail=win_detail or f"força ajustada {away_power:.2f} vs {home_power:.2f}",
        )
    if a_outlier and edge > -0.2 and home_power >= away_power * 0.92:
        return FavoriteRead(
            name=analysis.home_name,
            strength=0.45,
            basis="outlier_guard",
            detail="amostra visitante suspeita (placar outlier descartado)",
        )
    if h_outlier and edge < 0.2 and away_power >= home_power * 0.92:
        return FavoriteRead(
            name=analysis.away_name,
            strength=0.45,
            basis="outlier_guard",
            detail="amostra mandante suspeita (placar outlier descartado)",
        )
    return None


def _build_winner_reason(
    fav: FavoriteRead,
    analysis: FixtureAnalysis,
    home_profile,
    away_profile,
    *,
    scope: str,
    max_chars: int = 500,
) -> str:
    """Fundamentação objetiva do 1X2 — até max_chars, tom confiante e sóbrio."""
    is_home = fav.name == analysis.home_name
    fav_p = home_profile if is_home else away_profile
    opp_p = away_profile if is_home else home_profile
    opp_name = analysis.away_name if is_home else analysis.home_name

    fav_g = fav_p.avg_goals_scored
    fav_c = fav_p.avg_goals_conceded
    opp_g = opp_p.avg_goals_scored
    opp_c = opp_p.avg_goals_conceded
    n_min = min(fav_p.matches_sampled, opp_p.matches_sampled)

    lead = "Fora do filtro de gols, " if scope == "alternate" else ""
    sentences: list[str] = []

    venue = "em casa" if is_home else "fora de casa"

    # 1) Tese principal — ataque do favorito vs defesa adversária
    if fav_g >= opp_c + 0.25:
        sentences.append(
            f"{fav.name} marca em média {fav_g:.1f} gols/jogo e pega {opp_name} "
            f"sófrendo {opp_c:.1f} — o favorito {venue} tem onde explorar."
        )
    elif fav_g > opp_g and fav_c <= opp_c:
        sentences.append(
            f"{fav.name} equilibra melhor o duelo: {fav_g:.1f} marcados e {fav_c:.1f} sofridos "
            f"contra {opp_g:.1f} e {opp_c:.1f} de {opp_name}."
        )
    else:
        sentences.append(
            f"{fav.name} leva vantagem no confronto direto de forças ({fav.detail})."
        )

    # 2) Resultado recente (quando há amostra mínima)
    if n_min >= settings.min_sample_for_win_rate_favorite:
        wr_gap = fav_p.win_rate - opp_p.win_rate
        if wr_gap >= 0.10:
            sentences.append(
                f"Vitórias recentes: {fav_p.win_rate:.0%} ({fav.name}) vs {opp_p.win_rate:.0%} ({opp_name})."
            )

    # 3) Fechamento — a dica matadora
    if fav.basis == "combined":
        closer = (
            f"Produção ofensiva e resultado convergem para {fav.name} impor o ritmo e levar os 3 pontos."
        )
    elif fav.basis == "outlier_guard":
        closer = (
            f"Leitura corrigida após outlier na amostra adversária — {fav.name} é o lado mais confiável no 1X2."
        )
    else:
        if is_home:
            closer = (
                f"No mando de campo, {fav.name} tem margem estatística para controlar o jogo e vencer {opp_name}."
            )
        else:
            closer = (
                f"Mesmo fora de casa, {fav.name} traz números superiores para bater {opp_name} no 1X2."
            )
    sentences.append(closer)

    text = lead + " ".join(sentences)
    if len(text) > max_chars:
        text = text[: max_chars - 1].rstrip() + "…"
    return text


def _winner_pick(
    analysis: FixtureAnalysis,
    home_profile,
    away_profile,
    *,
    scope: str,
    strong_verdict_rate: float = 0.90,
) -> dict | None:
    fav = infer_favorite(analysis, home_profile, away_profile)
    if fav is None:
        return None

    n_min = min(home_profile.matches_sampled, away_profile.matches_sampled)
    verdict = "STRONG" if (fav.strength >= strong_verdict_rate and n_min >= 10) else "CANDIDATE"

    # Alternativa 1X2 só com favoritismo convincente — evita picks frágeis (ex.: edge mínimo)
    if scope == "alternate":
        if fav.basis == "outlier_guard" and fav.strength < 0.55:
            return None
        if fav.strength < 0.42:
            return None

    return {
        "market": f"VITÓRIA: {fav.name}",
        "verdict": verdict,
        "reason": _build_winner_reason(
            fav, analysis, home_profile, away_profile, scope=scope, max_chars=500
        ),
        "scope": scope,
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
    n_min = min(home_profile.matches_sampled, away_profile.matches_sampled)

    # Lógica de decisão para a ÚNICA melhor recomendação (PRIORIDADE TOTAL: GOLS)
    
    # 1. Prioridade Máxima: Chuva de Gols (Over 2.5) — barra mais alta em mata-mata
    o25_avg, o25_rate, o25_conf = knockout_over25_thresholds() if analysis.is_knockout else (3.2, 0.55, 95.0)
    if (
        combined_avg >= o25_avg
        and over_25_rate >= o25_rate
        and confidence >= o25_conf
        and n_min >= 2
    ):
        reason = (
            f"Média altíssima ({combined_avg:.1f}) e histórico de Over 2.5 em {over_25_rate:.0%}. "
            "Cenário de jogo muito aberto."
        )
        if analysis.is_knockout:
            reason += (
                " Mata-mata: só homologa Over 2.5 com perfil extremo; "
                "cenário base ainda é jogo fechado no 1º tempo."
            )
        return {
            "market": "OVER 2.5 GOALS",
            "verdict": "STRONG",
            "reason": reason,
            "scope": "goals",
        }

    # 2. Segurança no Over 1.5 (Core do Produto)
    if over_15_rate >= 0.72 and combined_avg >= 2.4 and confidence >= 90 and n_min >= 1:
        return {
            "market": "OVER 1.5 GOALS",
            "verdict": "STRONG" if n_min >= 2 else "CANDIDATE",
            "reason": f"Histórico sólido de pelo menos 2 gols ({over_15_rate:.0%}) com média combinada de {combined_avg:.1f}.",
            "scope": "goals",
        }

    # 3. Base de Segurança: Over 0.5 (Anti-Zero-Gols)
    if confidence >= 85 and n_min >= 1:
        return {
            "market": "OVER 0.5 GOALS",
            "verdict": "STRONG" if n_min >= 2 else "CANDIDATE",
            "reason": f"Filtro anti-zero-gols aprovado com Score {confidence}. Média combinada de {combined_avg:.1f} gols/jogo.",
            "scope": "goals",
        }

    # 4. Dominância Absoluta (1X2) - APENAS como último recurso se gols não forem claros e base for massiva
    winner = _winner_pick(
        analysis, home_profile, away_profile, scope="goals", strong_verdict_rate=0.95
    )
    if winner and winner["verdict"] == "STRONG" and n_min >= 10:
        return winner

    # Fallback para candidato a gol se nada acima for "STRONG"
    return {
        "market": "OVER 0.5 GOALS",
        "verdict": "CANDIDATE",
        "reason": f"Análise fundamentada para mercado de gols (Score {confidence}).",
        "scope": "goals",
    }


def _select_alternate_pick(
    analysis: FixtureAnalysis,
    home_profile,
    away_profile,
    combined_avg: float,
    max_zero_zero: float,
    avg_btts: float,
) -> dict | None:
    """Palpite fora do filtro de gols — 1X2 ou lay correct score."""
    # Vencedor entra aqui como alternativa na maioria dos casos
    winner = _winner_pick(analysis, home_profile, away_profile, scope="alternate")
    if winner:
        return winner

    home_win_rate = home_profile.win_rate
    away_win_rate = away_profile.win_rate
    n_min = min(home_profile.matches_sampled, away_profile.matches_sampled)

    # Critério estrito para Lay 0-0:
    # 1. Precisa ter pelo menos 2 jogos de amostra (segurança mínima)
    # 2. Risco de 0-0 deve ser moderado, não altíssimo (senão é perigoso até para Lay)
    # 3. Média de gols combinada não pode ser ridícula (ex: < 1.2)
    if n_min >= 2 and combined_avg >= 1.5 and max_zero_zero <= 0.30:
        return {
            "market": "LAY CORRECT SCORE: 0-0",
            "verdict": "CANDIDATE",
            "reason": (
                f"Leitura alternativa: Média combinada de {combined_avg:.1f} gols e histórico controlado de 0-0 ({max_zero_zero:.0%}). "
                "Cenário para buscar pelo menos um gol fora do filtro principal."
            ),
            "scope": "alternate",
        }

    # Se o jogo for muito ruim (ex: times que não marcam nada), retorna None (Descarte Total)
    return None


def analyze_upcoming(
    db: Session,
    limit: int = 50,
    *,
    for_today_only: bool = True,
    days: int | None = None,
    tz_name: str | None = None,
    competition_code: str | None = None,
) -> list[FixtureAnalysis]:
    query = _scheduled_fixtures_query(db, competition_code=competition_code)
    ctx = get_today_context(tz_name)
    if days is not None:
        end_utc = ctx.start_utc + timedelta(days=days)
        query = query.filter(Fixture.utc_date >= ctx.start_utc).filter(Fixture.utc_date < end_utc)
    elif for_today_only:
        query = query.filter(Fixture.utc_date >= ctx.start_utc).filter(Fixture.utc_date < ctx.end_utc)
    fixtures = query.order_by(Fixture.utc_date).limit(limit).all()
    return [analyze_fixture(db, fixture) for fixture in fixtures]


def persist_analysis(
    db: Session,
    analysis: FixtureAnalysis,
    llm_explanation: str | None = None,
    *,
    competition_code: str | None = None,
) -> None:
    from palpitaria.models import Fixture, FixtureReport
    from palpitaria.services.ai_tracker import record_ai_recommendation

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
    ctx = dict(analysis.match_context or {})
    if analysis.prediction:
        ctx["prediction"] = analysis.prediction
    report.match_context_json = json.dumps(ctx, ensure_ascii=False) if ctx else None
    analysis.match_context = ctx
    report.strategy_json = (
        json.dumps(analysis.strategy_card, ensure_ascii=False) if analysis.strategy_card else None
    )
    report.analyzed_at = datetime.utcnow()

    code = competition_code
    if not code:
        fixture = db.query(Fixture).filter_by(id=analysis.fixture_id).one_or_none()
        code = fixture.competition_code if fixture else settings.world_cup_code

    record_ai_recommendation(db, analysis, competition_code=code)
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
        # best_pick sempre recalculado em analyze_fixture (evita palpite obsoleto no banco)
        if report.match_context_json:
            analysis.match_context = json.loads(report.match_context_json)
        elif not analysis.excluded and report.llm_explanation:
            analysis.match_context = default_match_context()
        if report.strategy_json:
            analysis.strategy_card = json.loads(report.strategy_json)
        analysis.is_knockout = is_knockout_stage(analysis.stage) or bool(
            (analysis.match_context or {}).get("knockout")
        )


def count_teams_with_profiles(db: Session) -> tuple[int, int]:
    from palpitaria.models import Team

    total = db.query(Team).count()
    ready = 0
    for team in db.query(Team).all():
        profile = latest_profile(db, team.id)
        if profile and profile.matches_sampled >= 1:
            ready += 1
    return ready, total

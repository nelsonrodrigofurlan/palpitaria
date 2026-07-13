"""Tests for knockout phase tactical intelligence."""

from datetime import datetime

from palpitaria.services.analyzer import FixtureAnalysis, analyze_fixture
from palpitaria.services.knockout_climate import (
    adjust_best_pick_for_knockout,
    enrich_match_context_knockout,
    is_knockout_stage,
)


def test_is_knockout_stage_round_of_16():
    assert is_knockout_stage("ROUND_OF_16")
    assert is_knockout_stage("Last 16")
    assert not is_knockout_stage("GROUP_STAGE")
    assert not is_knockout_stage(None)


def test_adjust_over25_down_to_over15_in_knockout():
    pick = {
        "market": "OVER 2.5 GOALS",
        "verdict": "STRONG",
        "reason": "Média alta.",
        "scope": "goals",
    }
    adjusted = adjust_best_pick_for_knockout(pick, stage="QUARTER_FINALS")
    assert adjusted is not None
    assert adjusted["market"] == "OVER 1.5 GOALS"
    assert adjusted["verdict"] == "CANDIDATE"
    assert adjusted.get("knockout_adjusted_from") == "OVER 2.5 GOALS"


def test_no_adjustment_in_group_stage():
    pick = {"market": "OVER 2.5 GOALS", "verdict": "STRONG", "reason": "x", "scope": "goals"}
    assert adjust_best_pick_for_knockout(pick, stage="GROUP_STAGE") == pick


def test_enrich_match_context_adds_knockout_fields():
    ctx = enrich_match_context_knockout({"weather": "Seco"}, stage="SEMI_FINAL")
    assert ctx["knockout"] is True
    assert "knockout_climate" in ctx
    assert len(ctx["knockout_live_scenarios"]) >= 3


def test_fixture_analysis_flags_knockout(monkeypatch):
    """Smoke: is_knockout set when stage is eliminatória."""
    from types import SimpleNamespace

    fixture = SimpleNamespace(
        id=1,
        external_id=1,
        home_team_id=1,
        away_team_id=2,
        home_team=SimpleNamespace(name="A", external_id=1, crest_url=None),
        away_team=SimpleNamespace(name="B", external_id=2, crest_url=None),
        utc_date=datetime(2026, 7, 3, 18, 0),
        status="TIMED",
        stage="ROUND_OF_16",
        group_name=None,
        competition_code="WC",
        venue_stadium=None,
        venue_city=None,
        venue_state=None,
    )

    profile = SimpleNamespace(
        matches_sampled=3,
        avg_goals_scored=1.8,
        avg_goals_conceded=1.0,
        over_05_rate=0.9,
        over_15_rate=0.8,
        over_25_rate=0.6,
        zero_zero_rate=0.05,
        both_teams_score_rate=0.6,
        win_rate=0.5,
        insights_json=None,
        raw_json=None,
    )

    monkeypatch.setattr(
        "palpitaria.services.analyzer.latest_profile",
        lambda db, tid: profile,
    )
    monkeypatch.setattr(
        "palpitaria.services.analyzer.get_valid_insights_for_team",
        lambda db, tid: [],
    )

    analysis = analyze_fixture(None, fixture)  # type: ignore[arg-type]
    assert analysis.is_knockout is True

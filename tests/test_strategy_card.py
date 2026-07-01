"""Tests for structured strategy card."""

from datetime import datetime

from palpitaria.services.analyzer import FixtureAnalysis
from palpitaria.services.strategy_card import _fallback_strategy_card, build_strategy_card


def _sample_analysis(*, excluded: bool = False) -> FixtureAnalysis:
    return FixtureAnalysis(
        fixture_id=1,
        external_id=100,
        home_name="Estados Unidos",
        away_name="Bósnia",
        home_crest=None,
        away_crest=None,
        utc_date=datetime(2026, 7, 1, 0, 0, 0),
        status="TIMED",
        stage="Round of 16",
        group_name=None,
        goal_potential_score=72.0,
        excluded=excluded,
        best_pick={
            "market": "VITÓRIA: Estados Unidos",
            "verdict": "CANDIDATE",
            "reason": "Favorito com média ofensiva superior.",
            "scope": "alternate",
        },
    )


def test_fallback_strategy_card_for_excluded():
    card = _fallback_strategy_card(_sample_analysis(excluded=True))
    assert len(card["strategies"]) >= 1
    assert "VITÓRIA" in card["strategies"][0]["market"]


def test_build_strategy_card_without_llm_uses_fallback(monkeypatch):
    from palpitaria.config import settings

    monkeypatch.setattr(settings, "openai_api_key", "")
    card = build_strategy_card(_sample_analysis())
    assert card["strategies"]
    assert card["headline"]

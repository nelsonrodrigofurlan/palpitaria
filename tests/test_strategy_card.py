"""Tests for structured strategy card."""

from datetime import datetime

from palpitaria.services.analyzer import FixtureAnalysis
from palpitaria.services.strategy_card import (
    _fallback_strategy_card,
    _favorite_ml_price,
    build_strategy_card,
    compute_card_display_mode,
)


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


def _sample_odds(*, home_price: float = 1.25, away_price: float = 12.0) -> dict:
    return {
        "home": "Estados Unidos",
        "away": "Bósnia",
        "markets": [
            {
                "key": "h2h",
                "outcomes": [
                    {"name": "Estados Unidos", "price": home_price},
                    {"name": "Empate", "price": 6.0},
                    {"name": "Bósnia", "price": away_price},
                ],
            },
            {
                "key": "totals",
                "outcomes": [
                    {"name": "Over", "point": 2.5, "price": 1.95},
                    {"name": "Under", "point": 2.5, "price": 1.92},
                ],
            },
        ],
    }


def test_fallback_strategy_card_for_excluded():
    card = _fallback_strategy_card(_sample_analysis(excluded=True), odds=_sample_odds())
    assert len(card["strategies"]) >= 1
    assert card["display_mode"] == "handicap_primary"
    assert "Handicap" in card["strategies"][0]["market"]


def test_build_strategy_card_without_llm_uses_fallback(monkeypatch):
    from palpitaria.config import settings

    monkeypatch.setattr(settings, "openai_api_key", "")
    card = build_strategy_card(_sample_analysis(), odds=_sample_odds(home_price=2.05, away_price=4.0))
    assert card["strategies"]
    assert card["headline"]
    assert card["display_mode"] == "goals_primary"


def test_favorite_ml_triggers_handicap_mode():
    analysis = _sample_analysis()
    odds = _sample_odds(home_price=1.28)
    assert _favorite_ml_price(odds, analysis.home_name, analysis.away_name) == 1.28
    assert compute_card_display_mode(analysis, odds) == "handicap_primary"


def test_homologated_goals_mode():
    analysis = _sample_analysis()
    analysis.best_pick = {"market": "OVER 1.5", "verdict": "STRONG", "reason": "Médias altas."}
    odds = _sample_odds(home_price=2.1, away_price=3.5)
    assert compute_card_display_mode(analysis, odds) == "goals_primary"

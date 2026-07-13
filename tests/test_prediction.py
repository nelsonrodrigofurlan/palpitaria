"""Tests for Poisson prediction engine and competition profiles."""

from palpitaria.services.competitions import get_competition_profile
from palpitaria.services.prediction import edge, predict_match


def test_bsa_profile_min_sample():
    assert get_competition_profile("BSA").min_sample_games == 5
    assert get_competition_profile("BSB").home_advantage_goals > get_competition_profile("BSA").home_advantage_goals


def test_predict_high_scoring_prefers_over():
    pred = predict_match(
        home_scored=2.0,
        home_conceded=1.2,
        away_scored=1.6,
        away_conceded=1.4,
        competition_code="BSA",
        home_name="Flamengo",
        away_name="Bahia",
    )
    assert pred.p_over_15 > 0.5
    assert pred.lam_home > pred.lam_away  # home edge
    pick = pred.as_best_pick()
    assert pick is not None
    assert "OVER" in pick["market"] or pick["market"].startswith("VITÓRIA")


def test_knockout_compresses_and_avoids_over25():
    open_game = predict_match(
        home_scored=2.2,
        home_conceded=1.5,
        away_scored=1.8,
        away_conceded=1.6,
        competition_code="WC",
        stage="GROUP_STAGE",
    )
    ko = predict_match(
        home_scored=2.2,
        home_conceded=1.5,
        away_scored=1.8,
        away_conceded=1.6,
        competition_code="WC",
        stage="ROUND_OF_16",
    )
    assert ko.lam_home + ko.lam_away < open_game.lam_home + open_game.lam_away
    if ko.best_market:
        assert "2.5" not in ko.best_market or ko.verdict != "STRONG"


def test_edge_positive_when_model_above_implied():
    assert edge(0.70, 1.50) == 0.0333 or abs(edge(0.70, 1.50) - (0.70 - 1 / 1.5)) < 1e-3

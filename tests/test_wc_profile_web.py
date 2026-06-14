"""Tests for World Cup web profile enrichment."""

from datetime import datetime

from palpitaria.models import Team, TeamProfile
from palpitaria.services.ingest import latest_profile, save_team_profile
from palpitaria.services.wc_profile_web import (
    _compute_match_stats_by_name,
    _merge_match_lists,
    _web_match_to_api_shape,
    profile_needs_refresh,
)


def test_web_match_to_api_shape():
    entry = {
        "home_team": "Germany",
        "away_team": "Italy",
        "home_score": 2,
        "away_score": 2,
    }
    match = _web_match_to_api_shape(entry)
    assert match is not None
    assert match["score"]["fullTime"]["home"] == 2


def test_compute_match_stats_by_name():
    matches = [
        {
            "homeTeam": {"id": 0, "name": "Germany"},
            "awayTeam": {"id": 1, "name": "Italy"},
            "score": {"fullTime": {"home": 2, "away": 1}},
        },
        {
            "homeTeam": {"id": 0, "name": "France"},
            "awayTeam": {"id": 1, "name": "Germany"},
            "score": {"fullTime": {"home": 0, "away": 0}},
        },
    ]
    stats = _compute_match_stats_by_name(matches, "Alemanha", 759)
    assert stats["matches_sampled"] == 2
    assert stats["avg_goals_scored"] == 1.0
    assert stats["zero_zero_rate"] == 0.5


def test_merge_match_lists_deduplicates():
    m1 = {
        "homeTeam": {"id": 0, "name": "Japan"},
        "awayTeam": {"id": 1, "name": "Brazil"},
        "score": {"fullTime": {"home": 1, "away": 3}},
    }
    stats = _merge_match_lists([m1], [m1])
    assert len(stats) == 1


def test_latest_profile_ignores_zero_sample(db_session):
    team = Team(external_id=900, name="Testland")
    db_session.add(team)
    db_session.flush()

    empty = TeamProfile(
        team_id=team.id,
        computed_at=datetime.utcnow(),
        matches_sampled=0,
        raw_json='{"source":"api"}',
    )
    valid = TeamProfile(
        team_id=team.id,
        computed_at=datetime(2020, 1, 1),
        matches_sampled=5,
        avg_goals_scored=1.8,
        raw_json='{"source":"web_research","matches_sampled":5}',
    )
    db_session.add_all([empty, valid])
    db_session.commit()

    profile = latest_profile(db_session, team.id)
    assert profile is not None
    assert profile.matches_sampled == 5


def test_profile_needs_refresh(db_session):
    team = Team(external_id=902, name="Refreshland")
    db_session.add(team)
    db_session.flush()

    assert profile_needs_refresh(None) is True

    save_team_profile(
        db_session,
        team.id,
        {
            "matches_sampled": 5,
            "avg_goals_scored": 1.5,
            "avg_goals_conceded": 1.0,
            "zero_zero_rate": 0.1,
            "over_05_rate": 0.9,
            "over_15_rate": 0.7,
            "over_25_rate": 0.4,
            "win_rate": 0.5,
            "both_teams_score_rate": 0.6,
            "source": "hybrid",
            "api_matches": 1,
            "web_matches": 4,
        },
    )
    profile = latest_profile(db_session, team.id)
    assert profile_needs_refresh(profile, force=False) is False
    assert profile_needs_refresh(profile, force=True) is True

import pytest
from datetime import datetime
from unittest.mock import MagicMock
from palpitaria.services.ingest import ingest_world_cup, build_team_profiles
from palpitaria.services.analyzer import get_today_context
from palpitaria.models import Team, Fixture, TeamProfile

def test_ingest_world_cup(db_session):
    # Mock client
    mock_client = MagicMock()
    mock_client.get_competition_matches.return_value = [
        {
            "id": 1001,
            "utcDate": "2026-06-13T16:00:00Z",
            "status": "SCHEDULED",
            "matchday": 1,
            "stage": "GROUP_STAGE",
            "group": "Group A",
            "homeTeam": {"id": 1, "name": "Qatar", "crest": "qatar.png"},
            "awayTeam": {"id": 2, "name": "Switzerland", "crest": "swiss.png"},
            "score": {"fullTime": {"home": None, "away": None}}
        }
    ]
    mock_client.get_competition_teams.return_value = [
        {"id": 1, "name": "Qatar", "crest": "qatar.png"},
        {"id": 2, "name": "Switzerland", "crest": "swiss.png"}
    ]
    
    # Run ingest
    result = ingest_world_cup(db_session, client=mock_client)
    
    assert result["teams"] == 2
    assert result["fixtures"] == 1
    
    # Verify DB
    qatar = db_session.query(Team).filter_by(external_id=1).one()
    assert qatar.name == "Catar"
    assert qatar.crest_url == "qatar.png"
    
    fixture = db_session.query(Fixture).filter_by(external_id=1001).one()
    assert fixture.home_team_id == qatar.id

def test_build_team_profiles(db_session):
    # Setup teams + fixture today (only today's teams are synced)
    home = Team(external_id=1, name="Qatar")
    away = Team(external_id=2, name="Switzerland")
    db_session.add_all([home, away])
    db_session.flush()

    ctx = get_today_context()
    kickoff = ctx.start_utc.replace(hour=18, minute=0)
    db_session.add(
        Fixture(
            external_id=1001,
            competition_code="WC",
            season=2026,
            utc_date=kickoff,
            home_team_id=home.id,
            away_team_id=away.id,
            status="TIMED",
        )
    )
    db_session.commit()
    
    # Mock client
    mock_client = MagicMock()
    mock_client.get_team_matches.return_value = [
        {
            "homeTeam": {"id": 1},
            "awayTeam": {"id": 2},
            "score": {"fullTime": {"home": 1, "away": 0}}
        },
        {
            "homeTeam": {"id": 3},
            "awayTeam": {"id": 1},
            "score": {"fullTime": {"home": 2, "away": 2}}
        },
        {
            "homeTeam": {"id": 1},
            "awayTeam": {"id": 4},
            "score": {"fullTime": {"home": 0, "away": 0}}
        }
    ]
    
    updated = build_team_profiles(db_session, client=mock_client, today_only=True)
    
    assert updated == 2
    profile = db_session.query(TeamProfile).filter_by(team_id=home.id).one()
    assert profile.matches_sampled == 3
    assert profile.avg_goals_scored == pytest.approx((1 + 2 + 0) / 3, abs=1e-3)
    assert profile.zero_zero_rate == pytest.approx(1 / 3, abs=1e-3)


def test_build_team_profiles_skips_teams_not_playing_today(db_session):
    team = Team(external_id=99, name="Idle Team")
    db_session.add(team)
    db_session.commit()

    mock_client = MagicMock()
    updated = build_team_profiles(db_session, client=mock_client, today_only=True)

    assert updated == 0
    mock_client.get_team_matches.assert_not_called()

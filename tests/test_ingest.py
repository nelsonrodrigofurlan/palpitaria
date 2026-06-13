import pytest
from unittest.mock import MagicMock
from palpitaria.services.ingest import ingest_world_cup, build_team_profiles
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
    assert qatar.name == "Qatar"
    assert qatar.crest_url == "qatar.png"
    
    fixture = db_session.query(Fixture).filter_by(external_id=1001).one()
    assert fixture.home_team_id == qatar.id

def test_build_team_profiles(db_session):
    # Setup team
    team = Team(external_id=1, name="Qatar")
    db_session.add(team)
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
    
    # Run profile builder
    updated = build_team_profiles(db_session, client=mock_client)
    
    assert updated == 1
    profile = db_session.query(TeamProfile).filter_by(team_id=team.id).one()
    assert profile.matches_sampled == 3
    assert profile.avg_goals_scored == pytest.approx((1 + 2 + 0) / 3, abs=1e-3)
    assert profile.zero_zero_rate == pytest.approx(1 / 3, abs=1e-3)

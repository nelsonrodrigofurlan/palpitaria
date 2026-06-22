import pytest
from datetime import datetime
from unittest.mock import MagicMock
from palpitaria.services.ingest import ingest_competition, build_team_profiles
from palpitaria.services.analyzer import get_today_context
from palpitaria.models import Team, Fixture, TeamProfile

def test_ingest_competition(db_session):
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
    result = ingest_competition(db_session, client=mock_client, competition_code="TST")
    
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
            competition_code="TST",
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
    
    updated = build_team_profiles(db_session, client=mock_client, today_only=True, competition_code="TST")
    
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
    updated = build_team_profiles(db_session, client=mock_client, today_only=True, competition_code="TST")

    assert updated == 0
    mock_client.get_team_matches.assert_not_called()


def test_build_team_profiles_backfills_missing_profiles(db_session):
    """Seleções que já jogaram mas não têm perfil válido devem ser atualizadas mesmo sem jogo hoje."""
    home = Team(external_id=9763, name="Gana Test")
    away = Team(external_id=9999, name="Panamá Test")
    db_session.add_all([home, away])
    db_session.flush()

    db_session.add(
        Fixture(
            external_id=99999410,
            competition_code="TST",
            season=2026,
            utc_date=datetime(2026, 6, 17, 23, 0, 0),
            home_team_id=home.id,
            away_team_id=away.id,
            status="FINISHED",
            home_score=1,
            away_score=0,
        )
    )
    db_session.add(
        TeamProfile(
            team_id=away.id,
            computed_at=datetime.utcnow(),
            matches_sampled=3,
            avg_goals_scored=1.0,
            avg_goals_conceded=1.0,
            zero_zero_rate=0.0,
            over_05_rate=1.0,
            over_15_rate=0.5,
            over_25_rate=0.0,
            win_rate=0.33,
            both_teams_score_rate=0.5,
        )
    )
    db_session.add(
        TeamProfile(
            team_id=home.id,
            computed_at=datetime.utcnow(),
            matches_sampled=0,
            avg_goals_scored=0.0,
            avg_goals_conceded=0.0,
            zero_zero_rate=1.0,
            over_05_rate=0.0,
            over_15_rate=0.0,
            over_25_rate=0.0,
            win_rate=0.0,
            both_teams_score_rate=0.0,
        )
    )
    db_session.commit()

    mock_client = MagicMock()
    mock_client.get_team_matches.return_value = [
        {
            "homeTeam": {"id": 9763},
            "awayTeam": {"id": 9999},
            "score": {"fullTime": {"home": 1, "away": 0}},
        },
    ]

    updated = build_team_profiles(db_session, client=mock_client, today_only=True, competition_code="TST")

    assert updated == 1
    profile = (
        db_session.query(TeamProfile)
        .filter_by(team_id=home.id)
        .filter(TeamProfile.matches_sampled >= 1)
        .order_by(TeamProfile.computed_at.desc())
        .first()
    )
    assert profile is not None
    assert profile.matches_sampled >= 1
    assert profile.avg_goals_scored == pytest.approx(1.0, abs=1e-3)


def test_build_team_profiles_refreshes_stale_profile_on_new_day(db_session):
    """Perfil de dias atrás deve ser atualizado no dia do jogo — não é cache vitalício."""
    from datetime import timedelta

    home = Team(external_id=10, name="Brasil")
    away = Team(external_id=11, name="Teste")
    db_session.add_all([home, away])
    db_session.flush()

    ctx = get_today_context()
    kickoff = ctx.start_utc.replace(hour=20, minute=0)
    db_session.add(
        Fixture(
            external_id=2001,
            competition_code="TST",
            season=2026,
            utc_date=kickoff,
            home_team_id=home.id,
            away_team_id=away.id,
            status="TIMED",
        )
    )
    db_session.add(
        TeamProfile(
            team_id=home.id,
            computed_at=datetime.utcnow() - timedelta(days=3),
            matches_sampled=5,
            avg_goals_scored=1.5,
            avg_goals_conceded=0.8,
            zero_zero_rate=0.1,
            over_05_rate=0.9,
            over_15_rate=0.7,
            over_25_rate=0.4,
            win_rate=0.6,
            both_teams_score_rate=0.5,
        )
    )
    db_session.commit()

    mock_client = MagicMock()
    mock_client.get_team_matches.return_value = [
        {
            "homeTeam": {"id": 10},
            "awayTeam": {"id": 12},
            "score": {"fullTime": {"home": 2, "away": 1}},
        },
    ]

    updated = build_team_profiles(db_session, client=mock_client, today_only=True, competition_code="TST")

    assert updated == 2
    mock_client.get_team_matches.assert_called()
    profiles = (
        db_session.query(TeamProfile)
        .filter_by(team_id=home.id)
        .order_by(TeamProfile.computed_at.desc())
        .all()
    )
    assert len(profiles) == 2

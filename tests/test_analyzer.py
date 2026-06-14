from datetime import datetime, timedelta
from palpitaria.models import Team, Fixture, TeamProfile
from palpitaria.services.analyzer import analyze_fixture, get_today_context

def test_analyze_fixture_excluded_no_profile(db_session):
    # Setup
    home = Team(external_id=1, name="Home Team")
    away = Team(external_id=2, name="Away Team")
    db_session.add_all([home, away])
    db_session.flush()
    
    fixture = Fixture(
        external_id=101,
        competition_code="WC",
        season=2026,
        utc_date=datetime.utcnow(),
        home_team_id=home.id,
        away_team_id=away.id
    )
    db_session.add(fixture)
    db_session.commit()
    
    # Test
    analysis = analyze_fixture(db_session, fixture)
    
    assert analysis.excluded is True
    assert "Perfil estatístico incompleto" in analysis.exclusion_reasons[0]

def test_analyze_fixture_candidate(db_session):
    # Setup
    home = Team(external_id=3, name="Offensive Home", crest_url="home.png")
    away = Team(external_id=4, name="Offensive Away", crest_url="away.png")
    db_session.add_all([home, away])
    db_session.flush()
    
    # Perfect profiles for over 0.5
    h_profile = TeamProfile(
        team_id=home.id,
        matches_sampled=10,
        avg_goals_scored=2.0,
        avg_goals_conceded=1.0,
        zero_zero_rate=0.0,
        over_05_rate=1.0,
        over_15_rate=0.8,
        over_25_rate=0.4,
        win_rate=0.6,
        both_teams_score_rate=0.7
    )
    a_profile = TeamProfile(
        team_id=away.id,
        matches_sampled=10,
        avg_goals_scored=1.5,
        avg_goals_conceded=1.5,
        zero_zero_rate=0.05,
        over_05_rate=0.95,
        over_15_rate=0.7,
        over_25_rate=0.3,
        win_rate=0.4,
        both_teams_score_rate=0.6
    )
    db_session.add_all([h_profile, a_profile])
    
    fixture = Fixture(
        external_id=102,
        competition_code="WC",
        season=2026,
        utc_date=datetime.utcnow(),
        home_team_id=home.id,
        away_team_id=away.id
    )
    db_session.add(fixture)
    db_session.commit()
    
    # Test
    analysis = analyze_fixture(db_session, fixture)
    
    assert analysis.excluded is False
    assert analysis.goal_potential_score >= 80
    assert analysis.best_pick is not None
    assert analysis.best_pick.get("market")
    assert analysis.home_crest == "home.png"


def test_analyze_fixture_excluded_still_has_alternate_pick(db_session):
    home = Team(external_id=5, name="Germany")
    away = Team(external_id=6, name="Curaçao")
    db_session.add_all([home, away])
    db_session.flush()

    h_profile = TeamProfile(
        team_id=home.id,
        matches_sampled=8,
        avg_goals_scored=2.8,
        avg_goals_conceded=0.6,
        zero_zero_rate=0.05,
        over_05_rate=0.95,
        over_15_rate=0.85,
        over_25_rate=0.55,
        win_rate=0.85,
        both_teams_score_rate=0.20,
    )
    a_profile = TeamProfile(
        team_id=away.id,
        matches_sampled=6,
        avg_goals_scored=0.5,
        avg_goals_conceded=2.2,
        zero_zero_rate=0.10,
        over_05_rate=0.90,
        over_15_rate=0.65,
        over_25_rate=0.30,
        win_rate=0.15,
        both_teams_score_rate=0.15,
    )
    db_session.add_all([h_profile, a_profile])

    fixture = Fixture(
        external_id=103,
        competition_code="WC",
        season=2026,
        utc_date=datetime.utcnow(),
        home_team_id=home.id,
        away_team_id=away.id,
    )
    db_session.add(fixture)
    db_session.commit()

    analysis = analyze_fixture(db_session, fixture)

    assert analysis.excluded is True
    assert analysis.best_pick is not None
    assert analysis.best_pick.get("scope") == "alternate"
    market = analysis.best_pick.get("market", "")
    assert "VITÓRIA" in market or "LAY CORRECT SCORE" in market

    combined = next(c for c in analysis.criteria if c.name == "combined_avg_goals")
    assert combined.passed is True
    assert combined.level in ("ok", "strong")
    home_off = next(c for c in analysis.criteria if c.name == "home_offense")
    assert home_off.level == "strong"
    btts = next(c for c in analysis.criteria if c.name == "both_teams_score_rate")
    assert btts.level == "fail"

def test_today_context_logic():
    ctx = get_today_context("America/Sao_Paulo")
    assert ctx.timezone == "America/Sao_Paulo"
    assert ctx.start_utc < ctx.end_utc
    assert (ctx.end_utc - ctx.start_utc).total_seconds() == 86400

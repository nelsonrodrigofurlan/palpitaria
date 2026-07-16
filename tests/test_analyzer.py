from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from palpitaria.models import Team, Fixture, TeamProfile
from palpitaria.services.analyzer import (
    analyze_fixture,
    build_criteria_brief,
    count_teams_with_profiles,
    get_today_context,
)


def test_count_teams_with_profiles_scoped_to_competition(db_session):
    bsa_home = Team(external_id=901, name="BSA Home")
    bsa_away = Team(external_id=902, name="BSA Away")
    bsb_home = Team(external_id=903, name="BSB Home")
    bsb_away = Team(external_id=904, name="BSB Away")
    db_session.add_all([bsa_home, bsa_away, bsb_home, bsb_away])
    db_session.flush()

    db_session.add_all(
        [
            Fixture(
                external_id=901,
                competition_code="TEST_A",
                season=2026,
                utc_date=datetime.utcnow(),
                home_team_id=bsa_home.id,
                away_team_id=bsa_away.id,
            ),
            Fixture(
                external_id=902,
                competition_code="TEST_B",
                season=2026,
                utc_date=datetime.utcnow(),
                home_team_id=bsb_home.id,
                away_team_id=bsb_away.id,
            ),
            TeamProfile(
                team_id=bsa_home.id,
                matches_sampled=5,
                avg_goals_scored=1.0,
                avg_goals_conceded=1.0,
                zero_zero_rate=0.0,
                over_05_rate=1.0,
                over_15_rate=0.5,
                over_25_rate=0.2,
                win_rate=0.4,
                both_teams_score_rate=0.5,
            ),
            TeamProfile(
                team_id=bsa_away.id,
                matches_sampled=5,
                avg_goals_scored=1.0,
                avg_goals_conceded=1.0,
                zero_zero_rate=0.0,
                over_05_rate=1.0,
                over_15_rate=0.5,
                over_25_rate=0.2,
                win_rate=0.4,
                both_teams_score_rate=0.5,
            ),
        ]
    )
    db_session.commit()

    assert count_teams_with_profiles(db_session, "TEST_A") == (2, 2)
    assert count_teams_with_profiles(db_session, "TEST_B") == (0, 2)

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
    assert "VITÓRIA: Alemanha" == market or market.startswith("VITÓRIA: Alemanha")
    assert "Cura" not in market

    combined = next(c for c in analysis.criteria if c.name == "combined_avg_goals")
    assert combined.passed is True
    assert combined.level in ("ok", "strong")
    home_off = next(c for c in analysis.criteria if c.name == "home_offense")
    assert home_off.level == "strong"
    btts = next(c for c in analysis.criteria if c.name == "both_teams_score_rate")
    assert btts.level == "fail"


def test_criteria_brief_argentina_algeria_shape(db_session):
    home = Team(external_id=10, name="Argentina")
    away = Team(external_id=11, name="Argélia")
    db_session.add_all([home, away])
    db_session.flush()

    db_session.add_all([
        TeamProfile(
            team_id=home.id,
            matches_sampled=1,
            avg_goals_scored=2.0,
            avg_goals_conceded=0.0,
            zero_zero_rate=0.0,
            over_05_rate=1.0,
            both_teams_score_rate=0.0,
            raw_json='{"source": "web_research", "recent_matches": [{"line": "10/06/26 — Argentina 2×0 Colômbia (2 mar, 0 lev)"}], "calc_matches": [{"line": "10/06/26 — Argentina 2×0 Colômbia (2 mar, 0 lev)"}]}',
        ),
        TeamProfile(
            team_id=away.id,
            matches_sampled=1,
            avg_goals_scored=3.0,
            avg_goals_conceded=0.0,
            zero_zero_rate=0.0,
            over_05_rate=1.0,
            both_teams_score_rate=0.0,
            raw_json='{"source": "web_research"}',
        ),
    ])
    fixture = Fixture(
        external_id=110,
        competition_code="WC",
        season=2026,
        utc_date=datetime.utcnow(),
        home_team_id=home.id,
        away_team_id=away.id,
    )
    db_session.add(fixture)
    db_session.commit()

    analysis = analyze_fixture(db_session, fixture)
    brief = analysis.criteria_brief

    assert brief is not None
    assert "Argentina x Argélia" in brief["match"]
    assert len(brief["lines"]) == 6
    assert "2.5 g/j" in brief["lines"][0]
    assert "0%" in brief["lines"][3]
    assert "gap" in brief["verdict"]
    assert brief["home_form"]["recent"][0].startswith("10/06")


def test_today_context_logic():
    tz = ZoneInfo("America/Sao_Paulo")
    # 22/06 10:00 SP → dia operacional 22/06, janela 22/06 06:00–23/06 06:00
    ctx = get_today_context("America/Sao_Paulo", now=datetime(2026, 6, 22, 10, 0, tzinfo=tz))
    assert ctx.date_local.isoformat() == "2026-06-22"
    assert ctx.timezone == "America/Sao_Paulo"
    assert ctx.start_utc < ctx.end_utc
    assert (ctx.end_utc - ctx.start_utc).total_seconds() == 86400

    # 22/06 03:00 SP (madrugada) → ainda dia operacional 21/06
    ctx_night = get_today_context("America/Sao_Paulo", now=datetime(2026, 6, 22, 3, 0, tzinfo=tz))
    assert ctx_night.date_local.isoformat() == "2026-06-21"

    # Jogo 23/06 00:00 SP entra no dia operacional 22/06
    kickoff = datetime(2026, 6, 23, 0, 0, tzinfo=tz).astimezone(ZoneInfo("UTC")).replace(tzinfo=None)
    assert ctx.start_utc <= kickoff < ctx.end_utc

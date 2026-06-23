import pytest

from palpitaria.models import Fixture, Team
from palpitaria.services.chat_service import (
    _message_suggests_deep_dive,
    build_chat_context,
    find_relevant_fixtures,
    teams_mentioned_in_message,
)
from palpitaria.services.team_names import localize_team_name


def _team(db_session, external_id: int) -> Team | None:
    return db_session.query(Team).filter_by(external_id=external_id).first()


def test_teams_mentioned_colombia_congo(db_session):
    if not _team(db_session, 818) or not _team(db_session, 1934):
        pytest.skip("Seleções Colômbia/Congo não estão no banco de teste")

    msg = "Vi que está pagando 55% para colombia contra o congo, acho entrada bacana"
    found = teams_mentioned_in_message(db_session, msg)
    names = {localize_team_name(t.name, t.external_id) for t in found}
    assert "Colômbia" in names
    assert "RD Congo" in names


def test_find_fixture_both_teams(db_session):
    home = _team(db_session, 818)
    away = _team(db_session, 1934)
    if not home or not away:
        pytest.skip("Seleções Colômbia/Congo não estão no banco de teste")

    from palpitaria.services.analyzer import get_today_context

    ctx = get_today_context()
    kickoff = ctx.start_utc.replace(hour=20, minute=0)
    db_session.add(
        Fixture(
            external_id=9999001,
            competition_code="TST",
            season=2026,
            utc_date=kickoff,
            home_team_id=home.id,
            away_team_id=away.id,
            status="TIMED",
        )
    )
    db_session.commit()

    teams = teams_mentioned_in_message(db_session, "Colômbia x Congo")
    fixtures = find_relevant_fixtures(db_session, teams)
    assert any(f.external_id == 9999001 for f in fixtures)


def test_build_chat_context_includes_fixture(db_session):
    home = _team(db_session, 818)
    away = _team(db_session, 1934)
    if not home or not away:
        pytest.skip("Seleções Colômbia/Congo não estão no banco de teste")

    from palpitaria.services.analyzer import get_today_context

    ctx = get_today_context()
    db_session.add(
        Fixture(
            external_id=9999002,
            competition_code="TST",
            season=2026,
            utc_date=ctx.start_utc.replace(hour=18),
            home_team_id=home.id,
            away_team_id=away.id,
            status="TIMED",
        )
    )
    db_session.commit()

    bundle = build_chat_context(db_session, "Colômbia contra Congo odd 55%", user_id=None)
    assert len(bundle["fixtures"]) >= 1
    assert "Colômbia" in bundle["mentioned_teams"]


def test_message_suggests_deep_dive():
    assert _message_suggests_deep_dive("Colômbia pagando 55%")
    assert not _message_suggests_deep_dive("Por que descartaram o jogo?")


def test_resolve_incorporate_blocks_proposals():
    from palpitaria.services.chat_service import _resolve_incorporate

    assert _resolve_incorporate({"incorporate": True, "insight_type": "proposal"}) is False
    assert _resolve_incorporate({"incorporate": True, "insight_type": "fact"}) is True
    assert _resolve_incorporate({"incorporate": True, "insight_type": "question"}) is False


def test_score_user_proposal_home_win(db_session):
    from datetime import datetime, timedelta

    from palpitaria.models import UserInsight
    from palpitaria.services.chat_service import _score_user_proposal

    home = _team(db_session, 818)
    away = _team(db_session, 1934)
    if not home or not away:
        pytest.skip("Seleções não estão no banco de teste")

    fx = Fixture(
        external_id=9999010,
        competition_code="TST",
        season=2026,
        utc_date=datetime.utcnow() - timedelta(days=1),
        home_team_id=home.id,
        away_team_id=away.id,
        status="FINISHED",
        home_score=2,
        away_score=0,
    )
    db_session.add(fx)
    db_session.flush()
    db_session.refresh(fx)
    ins = UserInsight(
        user_id=1,
        content="Colômbia para vencer, entrada bacana",
        insight_type="proposal",
        verdict="supports",
        fixture_id=fx.id,
        created_at=datetime.utcnow() - timedelta(days=1),
    )
    assert _score_user_proposal(ins, fx) == "green"

    fx.home_score = 0
    fx.away_score = 1
    ins2 = UserInsight(
        user_id=1,
        content="Colômbia para vencer",
        insight_type="proposal",
        verdict="supports",
        fixture_id=fx.id,
    )
    assert _score_user_proposal(ins2, fx) == "red"


def test_fetch_user_chat_history_two_day_window(db_session):
    from datetime import datetime, timedelta

    from palpitaria.models import User, UserInsight
    from palpitaria.services.chat_service import fetch_user_chat_history

    user = User(email="chat-test@example.com", hashed_password="x")
    db_session.add(user)
    db_session.flush()

    db_session.add(
        UserInsight(user_id=user.id, content="recente", created_at=datetime.utcnow() - timedelta(hours=1))
    )
    db_session.add(
        UserInsight(user_id=user.id, content="velho", created_at=datetime.utcnow() - timedelta(days=3))
    )
    db_session.commit()

    rows = fetch_user_chat_history(db_session, user.id)
    texts = [r.content for r in rows]
    assert "recente" in texts
    assert "velho" not in texts


def test_chat_daily_quota_three_per_day(db_session):
    from datetime import datetime

    from palpitaria.models import User, UserInsight
    from palpitaria.services.analyzer import get_today_context
    from palpitaria.services.chat_service import (
        CHAT_DAILY_LIMIT,
        is_chat_admin,
        user_chat_daily_quota,
    )

    assert is_chat_admin("nelson.r.furlan@gmail.com")
    assert not is_chat_admin("outro@example.com")

    admin_q = user_chat_daily_quota(db_session, 999, "nelson.r.furlan@gmail.com")
    assert admin_q["limited"] is False
    assert admin_q["blocked"] is False

    user = User(email="quota-test@example.com", hashed_password="x")
    db_session.add(user)
    db_session.flush()
    ctx = get_today_context()
    kickoff = ctx.start_utc.replace(hour=12)

    for i in range(CHAT_DAILY_LIMIT):
        db_session.add(
            UserInsight(user_id=user.id, content=f"msg {i}", created_at=kickoff)
        )
    db_session.commit()

    q = user_chat_daily_quota(db_session, user.id, user.email)
    assert q["used"] == CHAT_DAILY_LIMIT
    assert q["remaining"] == 0
    assert q["blocked"] is True

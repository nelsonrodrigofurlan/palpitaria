from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from palpitaria.services.ledger import (
    bet_local_period,
    close_past_months,
    compute_bet_pl,
    current_period,
    infer_branch_side,
    lay_liability,
    normalize_bet_side,
    period_label,
)


def test_period_label():
    assert period_label(2026, 6) == "Junho/2026"


def test_infer_branch_side():
    assert infer_branch_side("Lay Correct Score 0-0") == "LAY"
    assert infer_branch_side("Over 1.5 Goals", "over_1_5_1") == "BACK"


def test_normalize_bet_side_defaults_to_back():
    assert normalize_bet_side(None) == "BACK"
    assert normalize_bet_side("") == "BACK"
    assert normalize_bet_side("lay") == "LAY"


def test_compute_bet_pl_back():
    assert compute_bet_pl(100, 2.0, "WIN", 6.5, side="BACK") == pytest.approx(93.5)
    assert compute_bet_pl(100, 2.0, "LOSS", 6.5, side="BACK") == -100


def test_compute_bet_pl_lay():
    # Lay £10 @ 10.0 — green keeps stake minus commission; red pays liability
    assert compute_bet_pl(10, 10.0, "WIN", 5.0, side="LAY") == pytest.approx(9.5)
    assert compute_bet_pl(10, 10.0, "LOSS", 5.0, side="LAY") == pytest.approx(-90)
    assert lay_liability(10, 10.0) == 90


def test_bet_local_period_uses_app_timezone():
    # 2026-06-01 02:00 UTC = 2026-05-31 23:00 São Paulo
    utc = datetime(2026, 6, 1, 2, 0, 0)
    assert bet_local_period(utc) == (2026, 5)


def test_betfair_csv_net_pl_discounts_commission_on_green():
    from palpitaria.services.ledger import betfair_csv_net_pl

    # Export CSV mostraria Lucro/Perda = 30.00; líquido com 6,5% = 28.05
    assert betfair_csv_net_pl(100, 1.30, "WIN", 6.5, side="BACK") == pytest.approx(28.05)
    assert betfair_csv_net_pl(100, 1.30, "LOSS", 6.5, side="BACK") == -100


def test_close_past_months_archives_older_than_previous_month(db_session):
    from palpitaria.models import Bet, Branch, BranchMonthlySummary

    branch = Branch(name="Test Branch", slug="test_branch", description="x", commission_rate=6.5)
    db_session.add(branch)
    db_session.flush()

    # Abril 2026 — com corrente em junho, maio fica aberto; abril arquiva
    bet = Bet(
        branch_id=branch.id,
        description="Jogo teste ledger",
        odds=1.5,
        stake=100.0,
        outcome="WIN",
        profit_loss=46.5,
        created_at=datetime(2026, 4, 15, 15, 0, 0),
    )
    db_session.add(bet)
    db_session.commit()
    bet_id = bet.id

    original = current_period

    try:
        import palpitaria.services.ledger as ledger_mod

        ledger_mod.current_period = lambda: (2026, 6)
        created = close_past_months(db_session)
    finally:
        import palpitaria.services.ledger as ledger_mod

        ledger_mod.current_period = original

    assert len(created) == 1
    assert created[0].year == 2026
    assert created[0].month == 4
    assert db_session.query(Bet).filter_by(id=bet_id).count() == 1
    assert db_session.query(BranchMonthlySummary).filter_by(branch_id=branch.id, year=2026, month=4).count() == 1


def test_close_past_months_keeps_previous_month_open(db_session):
    from palpitaria.models import Bet, Branch

    branch = Branch(name="Test Branch 2", slug="test_branch_2", description="x", commission_rate=6.5)
    db_session.add(branch)
    db_session.flush()

    bet = Bet(
        branch_id=branch.id,
        description="Maio ainda aberto",
        odds=1.5,
        stake=100.0,
        outcome="WIN",
        profit_loss=46.5,
        created_at=datetime(2026, 5, 15, 15, 0, 0),
    )
    db_session.add(bet)
    db_session.commit()
    bet_id = bet.id

    original = current_period

    try:
        import palpitaria.services.ledger as ledger_mod

        ledger_mod.current_period = lambda: (2026, 6)
        created = close_past_months(db_session)
    finally:
        import palpitaria.services.ledger as ledger_mod

        ledger_mod.current_period = original

    assert created == []
    assert db_session.query(Bet).filter_by(id=bet_id).count() == 1

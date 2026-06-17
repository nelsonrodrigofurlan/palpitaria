import pytest

from palpitaria.services.ai_tracker import compute_accuracy_stats, evaluate_market, normalize_market


@pytest.mark.parametrize(
    "market,home,away,hs,as_,expected",
    [
        ("OVER 1.5 GOALS", "A", "B", 2, 1, "HIT"),
        ("OVER 1,5 GOALS", "A", "B", 1, 0, "MISS"),
        ("OVER 0.5 GOALS", "A", "B", 0, 0, "MISS"),
        ("OVER 2.5 GOALS", "A", "B", 2, 1, "HIT"),
        ("VITÓRIA: Portugal", "Portugal", "RD Congo", 3, 0, "HIT"),
        ("VITÓRIA: RD Congo", "Portugal", "RD Congo", 3, 0, "MISS"),
        ("LAY CORRECT SCORE: 0-0", "A", "B", 1, 0, "HIT"),
        ("LAY CORRECT SCORE: 0-0", "A", "B", 0, 0, "MISS"),
        ("LAY CORRECT SCORE: 1-0", "A", "B", 1, 0, "MISS"),
        ("LAY CORRECT SCORE: 1-0", "A", "B", 2, 0, "HIT"),
    ],
)
def test_evaluate_market(market, home, away, hs, as_, expected):
    assert evaluate_market(market, home_name=home, away_name=away, home_score=hs, away_score=as_) == expected


def test_compute_accuracy_stats_dedupes_by_fixture():
    from datetime import datetime
    from types import SimpleNamespace

    recs = [
        SimpleNamespace(
            fixture_id=1,
            analyzed_at=datetime(2026, 6, 16, 10),
            outcome="MISS",
            market="OVER 1.5 GOALS",
        ),
        SimpleNamespace(
            fixture_id=1,
            analyzed_at=datetime(2026, 6, 16, 18),
            outcome="HIT",
            market="OVER 1.5 GOALS",
        ),
        SimpleNamespace(
            fixture_id=2,
            analyzed_at=datetime(2026, 6, 17, 10),
            outcome="PENDING",
            market="VITÓRIA: Brasil",
        ),
    ]
    stats = compute_accuracy_stats(recs)
    assert stats["total"] == 2
    assert stats["hits"] == 1
    assert stats["misses"] == 0
    assert stats["pending"] == 1
    assert stats["hit_rate_pct"] == 100

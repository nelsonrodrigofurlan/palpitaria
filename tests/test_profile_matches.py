from palpitaria.services.profile_matches import build_matches_snapshot, snapshot_match


def test_snapshot_match_home_team():
    match = {
        "utcDate": "2026-03-22T20:00:00Z",
        "homeTeam": {"id": 10, "name": "Argentina"},
        "awayTeam": {"id": 11, "name": "Chile"},
        "score": {"fullTime": {"home": 2, "away": 1}},
    }
    snap = snapshot_match(match, "Argentina", 10)
    assert snap is not None
    assert snap["scored"] == 2
    assert snap["conceded"] == 1
    assert "2×1" in snap["result"]
    assert "22/03" in snap["line"]


def test_build_matches_snapshot_last_three():
    matches = [
        {
            "utcDate": "2026-01-10T20:00:00Z",
            "homeTeam": {"id": 10, "name": "Argentina"},
            "awayTeam": {"id": 1, "name": "Brazil"},
            "score": {"fullTime": {"home": 1, "away": 0}},
        },
        {
            "utcDate": "2026-03-22T20:00:00Z",
            "homeTeam": {"id": 10, "name": "Argentina"},
            "awayTeam": {"id": 2, "name": "Chile"},
            "score": {"fullTime": {"home": 2, "away": 1}},
        },
        {
            "utcDate": "2026-02-15T20:00:00Z",
            "homeTeam": {"id": 3, "name": "Uruguay"},
            "awayTeam": {"id": 10, "name": "Argentina"},
            "score": {"fullTime": {"home": 0, "away": 3}},
        },
        {
            "utcDate": "2026-04-01T20:00:00Z",
            "homeTeam": {"id": 10, "name": "Argentina"},
            "awayTeam": {"id": 4, "name": "Paraguay"},
            "score": {"fullTime": {"home": 3, "away": 0}},
        },
    ]
    recent = build_matches_snapshot(matches, "Argentina", 10, limit=3)
    assert len(recent) == 3
    assert "Paraguay" in recent[0]["result"]
    assert "Chile" in recent[1]["result"]

"""Foundation gate: provisional / odds-implied profiles block public picks."""

from types import SimpleNamespace

from palpitaria.services.foundation import both_profiles_solid, profile_has_solid_foundation


def test_odds_implied_blocked():
    profile = SimpleNamespace(
        matches_sampled=5,
        raw_json='{"source": "odds_implied", "kind": "club_provisional"}',
    )
    ok, reason = profile_has_solid_foundation(profile, min_matches=5)
    assert ok is False
    assert "provis" in reason.lower() or "fundamento" in reason.lower() or "odds" in reason.lower()


def test_api_profile_ok():
    profile = SimpleNamespace(
        matches_sampled=8,
        raw_json='{"source": "api", "api_matches": 8}',
    )
    ok, _ = profile_has_solid_foundation(profile, min_matches=5)
    assert ok is True


def test_both_must_be_solid():
    solid = SimpleNamespace(matches_sampled=6, raw_json='{"source": "hybrid"}')
    weak = SimpleNamespace(matches_sampled=6, raw_json='{"source": "odds_implied"}')
    ok, reasons = both_profiles_solid(solid, weak, min_matches=5)
    assert ok is False
    assert reasons

"""Tests for CBF Copa do Brasil HTML parser."""

from pathlib import Path

from palpitaria.services.cbf_cdb_ingest import parse_cbf_cdb_matches, resolve_cbf_team_name

SAMPLE = Path(__file__).resolve().parents[1] / "data" / "_cdb_cbf_sample.html"


def test_resolve_cbf_team_aliases():
    assert resolve_cbf_team_name("Vasco da Gama Saf") == "CR Vasco da Gama"
    assert resolve_cbf_team_name("Fortaleza Saf") == "Fortaleza"
    assert resolve_cbf_team_name("Athletico Paranaense") == "CA Paranaense"


def test_parse_cbf_sample_has_oitavas():
    if not SAMPLE.exists():
        return  # sample gerado sob demanda pelo probe
    html = SAMPLE.read_text(encoding="utf-8")
    matches = parse_cbf_cdb_matches(html)
    assert len(matches) >= 16
    vasco = next(m for m in matches if "Vasco" in m.home_name)
    assert vasco.away_name.startswith("Fluminense")
    assert vasco.cbf_game_id == 834851
    assert vasco.kickoff_local.day == 1
    assert vasco.kickoff_local.month == 8
    assert vasco.kickoff_local.hour == 17
    assert vasco.leg == "Ida"

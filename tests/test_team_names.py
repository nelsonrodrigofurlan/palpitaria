"""Tests for Brazilian Portuguese team name localization."""

from palpitaria.services.team_names import (
    english_team_name,
    localize_team_name,
    names_for_matching,
)


def test_localize_by_external_id():
    assert localize_team_name("Germany", 759) == "Alemanha"
    assert localize_team_name("Ivory Coast", 1935) == "Costa do Marfim"
    assert localize_team_name("Netherlands", 8601) == "Holanda"
    assert localize_team_name("United States", 771) == "Estados Unidos"


def test_localize_by_english_name():
    assert localize_team_name("South Korea") == "Coreia do Sul"
    assert localize_team_name("Czechia") == "República Tcheca"
    assert localize_team_name("Brazil") == "Brasil"


def test_english_for_web_search():
    assert english_team_name("Alemanha", 759) == "Germany"
    assert english_team_name("Holanda", 8601) == "Netherlands"


def test_names_for_matching_bilingual():
    variants = names_for_matching("Alemanha", 759)
    assert "germany" in variants
    assert "alemanha" in variants

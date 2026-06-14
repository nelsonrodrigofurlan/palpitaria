"""Site-targeted DuckDuckGo queries for Copa do Mundo stalking (core-6 sources)."""

from __future__ import annotations

from palpitaria.services.team_names import english_team_name

# Core-6 + cobertura diária (ver copa-mundo-stalking.md)
STALKING_SITES = {
    "fifa": "site:fifa.com",
    "espn": "site:espn.com",
    "transfermarkt": "site:transfermarkt.com",
    "fbref": "site:fbref.com",
    "soccerway": "site:soccerway.com",
    "worldfootball": "site:worldfootball.net",
    "bbc": "site:bbc.com",
    "sky": "site:skysports.com",
    "elo": "site:eloratings.net",
}


def team_bastidores_queries(team_name: str, *, external_id: int | None = None) -> list[str]:
    """Lesões, convocação, bastidores — prioriza fontes especializadas."""
    en = english_team_name(team_name, external_id)
    s = STALKING_SITES
    return [
        f"{en} {s['transfermarkt']} injuries squad World Cup 2026",
        f"{en} {s['fifa']} national team news World Cup 2026",
        f"{team_name} {s['espn']} seleção Copa 2026 lesões escalação",
        f"{en} {s['bbc']} OR {s['sky']} national team news",
        f"{team_name} seleção Copa do Mundo 2026 bastidores lesões",
    ]


def team_results_queries(team_name: str, *, external_id: int | None = None) -> list[str]:
    """Placares recentes — amistosos, eliminatórias, Nations League."""
    en = english_team_name(team_name, external_id)
    s = STALKING_SITES
    return [
        f"{en} {s['soccerway']} national team results 2025 2026",
        f"{en} {s['worldfootball']} matches friendly qualifier",
        f"{en} {s['fbref']} national team stats goals",
        f"{team_name} seleção últimos jogos placares amistosos eliminatórias",
        f"{en} World Cup qualifying Nations League friendly results score",
        f"{en} national team last 10 matches results score 2024 2025 2026",
    ]


def match_context_queries(
    home_name: str,
    away_name: str,
    *,
    home_external_id: int | None = None,
    away_external_id: int | None = None,
) -> list[str]:
    """Clima, árbitro, gramado, preview do jogo."""
    home_en = english_team_name(home_name, home_external_id)
    away_en = english_team_name(away_name, away_external_id)
    s = STALKING_SITES
    return [
        f"{home_en} vs {away_en} {s['fifa']} World Cup 2026 match preview",
        f"{home_en} {away_en} {s['espn']} referee weather stadium forecast",
        f"{home_name} x {away_name} Copa 2026 árbitro clima gramado estádio",
        f"{home_en} vs {away_en} {s['bbc']} OR {s['sky']} World Cup preview",
    ]


def team_strength_queries(team_name: str, *, external_id: int | None = None) -> list[str]:
    """Ranking / força — contexto opcional para LLM."""
    en = english_team_name(team_name, external_id)
    return [
        f"{en} {STALKING_SITES['elo']} World Cup",
        f"{en} FIFA world ranking 2026",
    ]

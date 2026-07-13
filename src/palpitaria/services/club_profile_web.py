"""Perfil web para clubes — fallback quando football-data não cobre a liga (BSB free)."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy.orm import Session

from palpitaria.models import Team, TeamProfile
from palpitaria.services.ingest import latest_profile, save_team_profile
from palpitaria.services.llm_client import chat_completion
from palpitaria.services.llm_utils import _parse_json_from_llm
from palpitaria.services.profile_matches import build_matches_snapshot
from palpitaria.services.scraper import search_web_stalking
from palpitaria.services.wc_profile_web import (
    _compute_match_stats_by_name,
    _filter_implausible_matches,
    _web_match_to_api_shape,
    teams_playing_today,
)

CLUB_RESULTS_SYSTEM = """Você extrai resultados REAIS de jogos de CLUBES a partir de snippets da web.

Regras OBRIGATÓRIAS:
- Inclua APENAS jogos com placar explícito (ex.: 2-1, 2 x 1).
- NÃO invente jogos, placares ou datas.
- Priorize Brasileirão Série A/B 2025-2026, Copa do Brasil, estaduais recentes.
- Só jogos do clube pedido (time profissional).
- Se não houver placares claros, retorne matches: [].

Retorne SOMENTE JSON válido:
{
  "matches": [
    {
      "date": "YYYY-MM-DD ou desconhecida",
      "home_team": "mandante",
      "away_team": "visitante",
      "home_score": 2,
      "away_score": 1,
      "competition": "brasileirao|serie_b|copa_brasil|estadual|other"
    }
  ],
  "confidence": 0-100,
  "sources_summary": "nota breve"
}
"""


def club_results_queries(team_name: str) -> list[str]:
    return [
        f"{team_name} últimos jogos resultados placares 2026",
        f"{team_name} Brasileirão Série B resultados recentes",
        f"{team_name} últimos 5 jogos placar",
    ]


def build_club_web_profile(
    db: Session,
    team: Team,
    *,
    log_callback=None,
) -> TeamProfile | None:
    def log(msg: str) -> None:
        if log_callback:
            log_callback(msg)

    log(f"  Web clube: histórico — {team.name}...")
    snippets = search_web_stalking(club_results_queries(team.name), max_results_per_query=4)
    if not snippets or len(snippets) < 80:
        log(f"    -> poucas fontes para {team.name}")
        return None

    try:
        response = chat_completion(
            CLUB_RESULTS_SYSTEM,
            f"Clube: {team.name}\n\nFontes:\n{snippets}",
            temperature=0.1,
            max_tokens=2200,
            feature="club_profile",
        )
        parsed = _parse_json_from_llm(response) or {}
    except Exception as exc:
        log(f"    !! LLM: {exc}")
        return None

    web_matches = []
    for entry in parsed.get("matches") or []:
        shaped = _web_match_to_api_shape(entry)
        if shaped:
            web_matches.append(shaped)
    web_matches = _filter_implausible_matches(web_matches, team.name, team.external_id)
    if not web_matches:
        log(f"    -> sem placares válidos para {team.name}")
        return None

    stats = _compute_match_stats_by_name(web_matches, team.name, team.external_id)
    if stats.get("matches_sampled", 0) < 1:
        log(f"    -> amostra 0 para {team.name}")
        return None

    stats["source"] = "web_research"
    stats["kind"] = "club"
    stats["web_matches"] = len(web_matches)
    stats["confidence"] = parsed.get("confidence", 0)
    stats["sources_summary"] = parsed.get("sources_summary", "")
    stats["recent_matches"] = build_matches_snapshot(
        web_matches, team.name, team.external_id, limit=3
    )
    stats["calc_matches"] = build_matches_snapshot(
        web_matches, team.name, team.external_id, limit=max(stats["matches_sampled"], 3)
    )

    profile = save_team_profile(db, team.id, stats, preserve_insights=True)
    log(
        f"    -> {stats['matches_sampled']} jogos | GF {stats['avg_goals_scored']:.2f} "
        f"| GA {stats['avg_goals_conceded']:.2f}"
    )
    return profile


def enrich_club_profiles_today(
    db: Session,
    competition_code: str,
    *,
    log_callback=None,
    force: bool = False,
) -> int:
    teams = teams_playing_today(db, competition_code=competition_code)
    if not teams:
        if log_callback:
            log_callback(f"Web clube: nenhum jogo de {competition_code} hoje.")
        return 0

    updated = 0
    for team in teams:
        existing = latest_profile(db, team.id)
        if (
            not force
            and existing
            and existing.matches_sampled >= 3
            and (existing.raw_json or "").find("web_research") >= 0
        ):
            if log_callback:
                log_callback(f"  Skip {team.name} (perfil web ok)")
            continue
        if build_club_web_profile(db, team, log_callback=log_callback):
            updated += 1
    db.commit()
    return updated

"""Web + API hybrid profiles for World Cup national teams."""

from __future__ import annotations

import json
import time
import unicodedata
from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from palpitaria.config import settings
from palpitaria.models import Fixture, Team, TeamProfile
from palpitaria.services.analyzer import _scheduled_fixtures_query, get_today_context
from palpitaria.services.football_data_client import FootballDataClient
from palpitaria.services.ingest import (
    _extract_score,
    latest_profile,
    save_team_profile,
)
from palpitaria.services.llm_client import chat_completion
from palpitaria.services.scraper import _parse_json_from_llm, search_web_stalking
from palpitaria.services.team_names import english_team_name, names_for_matching
from palpitaria.services.wc_stalking_queries import team_results_queries

TEAM_RESULTS_SYSTEM = """Você extrai resultados REAIS de jogos de seleções a partir de snippets da web.

Regras OBRIGATÓrias:
- Inclua APENAS jogos com placar explícito nas fontes (ex.: 2-1, 2 x 1, vitória por 3 gols a 0).
- NÃO invente jogos, placares ou datas.
- Priorize: Copa 2026, eliminatórias, Nations League, amistosos 2024-2026.
- Ignore jogos de clubes — só seleção principal (A).
- Se não houver placares claros, retorne matches: [].

Retorne SOMENTE JSON válido:
{
  "matches": [
    {
      "date": "YYYY-MM-DD ou desconhecida",
      "home_team": "nome mandante",
      "away_team": "nome visitante",
      "home_score": 2,
      "away_score": 1,
      "competition": "friendly|qualifier|nations_league|world_cup|other"
    }
  ],
  "confidence": 0-100,
  "sources_summary": "breve nota sobre quais fontes tinham placares"
}
"""


def get_team_results_queries(team_name: str, external_id: int | None = None) -> list[str]:
    return team_results_queries(team_name, external_id=external_id)


def _normalize_name(name: str) -> str:
    lowered = name.lower().strip()
    nfkd = unicodedata.normalize("NFKD", lowered)
    return "".join(c for c in nfkd if not unicodedata.combining(c))


def _team_matches_name(team_name: str, candidate: str, external_id: int | None = None) -> bool:
    cand = _normalize_name(candidate)
    if not cand:
        return False
    for variant in names_for_matching(team_name, external_id):
        if variant == cand or variant in cand or cand in variant:
            return True
    return False


def _web_match_to_api_shape(entry: dict) -> dict | None:
    home_score = entry.get("home_score")
    away_score = entry.get("away_score")
    if home_score is None or away_score is None:
        return None
    try:
        home_i = int(home_score)
        away_i = int(away_score)
    except (TypeError, ValueError):
        return None
    home_team = str(entry.get("home_team") or "").strip()
    away_team = str(entry.get("away_team") or "").strip()
    if not home_team or not away_team:
        return None
    return {
        "homeTeam": {"id": 0, "name": home_team},
        "awayTeam": {"id": 1, "name": away_team},
        "score": {"fullTime": {"home": home_i, "away": away_i}},
    }


def _compute_match_stats_by_name(
    matches: list[dict], team_name: str, external_id: int | None = None
) -> dict:
    """Same metrics as API path, matching team by name instead of external_id."""
    sampled = 0
    goals_scored = 0
    goals_conceded = 0
    zero_zero = 0
    over_05 = 0
    over_15 = 0
    over_25 = 0
    wins = 0
    btts = 0

    for match in matches:
        home_name = match["homeTeam"]["name"]
        away_name = match["awayTeam"]["name"]
        home = _extract_score(match, "home")
        away = _extract_score(match, "away")
        if home is None or away is None:
            continue
        if not (_team_matches_name(team_name, home_name, external_id) or _team_matches_name(team_name, away_name, external_id)):
            continue

        sampled += 1
        if _team_matches_name(team_name, home_name, external_id):
            scored, conceded = home, away
            if home > away:
                wins += 1
        else:
            scored, conceded = away, home
            if away > home:
                wins += 1

        goals_scored += scored
        goals_conceded += conceded
        total = home + away
        if total == 0:
            zero_zero += 1
        if total >= 1:
            over_05 += 1
        if total >= 2:
            over_15 += 1
        if total >= 3:
            over_25 += 1
        if home > 0 and away > 0:
            btts += 1

    if sampled == 0:
        return {
            "matches_sampled": 0,
            "avg_goals_scored": 0.0,
            "avg_goals_conceded": 0.0,
            "zero_zero_rate": 1.0,
            "over_05_rate": 0.0,
            "over_15_rate": 0.0,
            "over_25_rate": 0.0,
            "win_rate": 0.0,
            "both_teams_score_rate": 0.0,
        }

    return {
        "matches_sampled": sampled,
        "avg_goals_scored": round(goals_scored / sampled, 3),
        "avg_goals_conceded": round(goals_conceded / sampled, 3),
        "zero_zero_rate": round(zero_zero / sampled, 3),
        "over_05_rate": round(over_05 / sampled, 3),
        "over_15_rate": round(over_15 / sampled, 3),
        "over_25_rate": round(over_25 / sampled, 3),
        "win_rate": round(wins / sampled, 3),
        "both_teams_score_rate": round(btts / sampled, 3),
    }


def _match_dedup_key(match: dict) -> str:
    home = _normalize_name(match["homeTeam"]["name"])
    away = _normalize_name(match["awayTeam"]["name"])
    h = _extract_score(match, "home")
    a = _extract_score(match, "away")
    pair = tuple(sorted([home, away]))
    return f"{pair[0]}|{pair[1]}|{h}-{a}"


def _team_goals_in_match(match: dict, team_name: str, external_id: int | None) -> int | None:
    home = _extract_score(match, "home")
    away = _extract_score(match, "away")
    if home is None or away is None:
        return None
    if _team_matches_name(team_name, match["homeTeam"]["name"], external_id):
        return home
    if _team_matches_name(team_name, match["awayTeam"]["name"], external_id):
        return away
    return None


def _filter_implausible_matches(
    matches: list[dict], team_name: str, external_id: int | None = None
) -> list[dict]:
    """Drop outlier scores (wrong team / category) that poison tiny samples."""
    cap = settings.max_plausible_team_goals_per_match
    kept: list[dict] = []
    for match in matches:
        scored = _team_goals_in_match(match, team_name, external_id)
        if scored is not None and scored > cap:
            continue
        kept.append(match)
    return kept


def _merge_match_lists(*lists: list[dict]) -> list[dict]:
    seen: set[str] = set()
    merged: list[dict] = []
    for batch in lists:
        for match in batch:
            key = _match_dedup_key(match)
            if key in seen:
                continue
            seen.add(key)
            merged.append(match)
    return merged


def extract_web_matches(team_name: str, raw_content: str) -> dict:
    user = f"Seleção: {team_name}\n\nFontes da web:\n{raw_content}"
    try:
        response = chat_completion(TEAM_RESULTS_SYSTEM, user, temperature=0.1, max_tokens=2500)
        parsed = _parse_json_from_llm(response)
        if not parsed:
            return {"matches": [], "confidence": 0, "sources_summary": "parse failed"}
        return parsed
    except Exception as exc:
        return {"matches": [], "confidence": 0, "sources_summary": str(exc)}


def fetch_api_finished_matches(team: Team) -> list[dict]:
    if not settings.has_football_token:
        return []
    try:
        client = FootballDataClient()
        return client.get_team_matches(team.external_id, limit=30)
    except Exception:
        return []


def profile_needs_refresh(profile: TeamProfile | None, *, force: bool = False) -> bool:
    """Hybrid web+API profiles refresh on each analyze when force=True."""
    if force or profile is None:
        return True
    raw = json.loads(profile.raw_json or "{}")
    if raw.get("source") not in ("hybrid", "web_research"):
        return True
    if settings.wc_web_profile_refresh_hours <= 0:
        return False
    computed = profile.computed_at
    if computed.tzinfo is not None:
        computed = computed.replace(tzinfo=None)
    age = datetime.utcnow() - computed
    return age > timedelta(hours=settings.wc_web_profile_refresh_hours)


def teams_playing_today(db: Session, tz_name: str | None = None) -> list[Team]:
    ctx = get_today_context(tz_name)
    fixtures = (
        _scheduled_fixtures_query(db)
        .filter(Fixture.utc_date >= ctx.start_utc)
        .filter(Fixture.utc_date < ctx.end_utc)
        .all()
    )
    team_ids: set[int] = set()
    for fixture in fixtures:
        team_ids.add(fixture.home_team_id)
        team_ids.add(fixture.away_team_id)
    if not team_ids:
        return []
    return db.query(Team).filter(Team.id.in_(team_ids)).order_by(Team.name).all()


def build_web_team_profile(
    db: Session,
    team: Team,
    *,
    log_callback=None,
) -> TeamProfile | None:
    def log(msg: str) -> None:
        if log_callback:
            log_callback(msg)

    log(f"  Web: buscando histórico — {team.name}...")
    snippets = search_web_stalking(
        get_team_results_queries(team.name, team.external_id),
        max_results_per_query=4,
    )
    if not snippets or len(snippets) < 80:
        log(f"  Web: poucas fontes para {team.name}")
        return None

    extraction = extract_web_matches(team.name, snippets)
    web_entries = extraction.get("matches") or []
    web_matches = [_web_match_to_api_shape(m) for m in web_entries]
    web_matches = [m for m in web_matches if m is not None]

    api_matches = fetch_api_finished_matches(team)
    combined = _merge_match_lists(api_matches, web_matches)
    combined = _filter_implausible_matches(combined, team.name, team.external_id)
    if not combined:
        log(f"  Web: nenhum placar válido para {team.name}")
        return None

    stats = _compute_match_stats_by_name(combined, team.name, team.external_id)
    if api_matches and web_matches:
        source = "hybrid"
    elif api_matches:
        source = "api"
    else:
        source = "web_research"

    stats["source"] = source
    stats["api_matches"] = len([m for m in api_matches if _extract_score(m, "home") is not None])
    stats["web_matches"] = len(web_matches)
    stats["confidence"] = extraction.get("confidence", 0)
    stats["sources_summary"] = extraction.get("sources_summary", "")

    if stats["matches_sampled"] < settings.wc_web_profile_min_matches:
        log(
            f"  Web: amostra insuficiente para {team.name} "
            f"({stats['matches_sampled']}/{settings.wc_web_profile_min_matches} jogos)"
        )
        return None

    profile = save_team_profile(db, team.id, stats, preserve_insights=True)
    log(
        f"  Web: perfil {team.name} — {stats['matches_sampled']} jogos "
        f"(fonte: {source}, média {stats['avg_goals_scored']} gols/j)"
    )
    return profile


def enrich_today_team_profiles(
    db: Session,
    *,
    log_callback=None,
    tz_name: str | None = None,
    force_refresh: bool = False,
) -> int:
    """Always merge API + web history for today's teams (hybrid profile persists)."""
    if not settings.has_llm:
        if log_callback:
            log_callback("Web profiles: OPENAI_API_KEY necessária — pulando.")
        return 0

    teams = teams_playing_today(db, tz_name)
    if not teams:
        if log_callback:
            log_callback("Web profiles: nenhum jogo hoje.")
        return 0

    updated = 0
    for index, team in enumerate(teams):
        existing = latest_profile(db, team.id)
        if not profile_needs_refresh(existing, force=force_refresh):
            if log_callback:
                log_callback(f"  Skip {team.name} (híbrido recente)")
            continue
        if index > 0:
            time.sleep(2.0)
        profile = build_web_team_profile(db, team, log_callback=log_callback)
        if profile:
            updated += 1

    return updated

"""Snapshots de jogos recentes para transparência nos achados."""

from __future__ import annotations

import unicodedata
from datetime import datetime

from palpitaria.services.team_names import names_for_matching


def _extract_score(match: dict, side: str) -> int | None:
    score = match.get("score", {})
    full_time = score.get("fullTime") or score.get("regularTime") or {}
    value = full_time.get(side)
    return int(value) if value is not None else None


def _normalize_name(name: str) -> str:
    lowered = name.lower().strip()
    nfkd = unicodedata.normalize("NFKD", lowered)
    return "".join(c for c in nfkd if not unicodedata.combining(c))


def team_in_match(match: dict, team_name: str, external_id: int | None = None) -> bool:
    home = match.get("homeTeam") or {}
    away = match.get("awayTeam") or {}
    home_id = home.get("id")
    away_id = away.get("id")
    if external_id and (home_id == external_id or away_id == external_id):
        return True
    cand_home = _normalize_name(str(home.get("name") or ""))
    cand_away = _normalize_name(str(away.get("name") or ""))
    for variant in names_for_matching(team_name, external_id):
        if variant in (cand_home, cand_away) or variant in cand_home or variant in cand_away:
            return True
        if cand_home and (variant in cand_home or cand_home in variant):
            return True
        if cand_away and (variant in cand_away or cand_away in variant):
            return True
    return False


def _parse_match_date(match: dict) -> datetime | None:
    raw = match.get("utcDate") or match.get("date")
    if not raw:
        return None
    text = str(raw).strip()
    if not text or text.lower() in ("desconhecida", "unknown"):
        return None
    try:
        if "T" in text:
            return datetime.fromisoformat(text.replace("Z", "+00:00")).replace(tzinfo=None)
        return datetime.strptime(text[:10], "%Y-%m-%d")
    except ValueError:
        return None


def _format_date(match: dict) -> str:
    parsed = _parse_match_date(match)
    if parsed:
        return parsed.strftime("%d/%m/%y")
    return "—"


def snapshot_match(match: dict, team_name: str, external_id: int | None = None) -> dict | None:
    home_name = str((match.get("homeTeam") or {}).get("name") or "").strip()
    away_name = str((match.get("awayTeam") or {}).get("name") or "").strip()
    home = _extract_score(match, "home")
    away = _extract_score(match, "away")
    if home is None or away is None or not home_name or not away_name:
        return None
    if not team_in_match(match, team_name, external_id):
        return None

    home_id = (match.get("homeTeam") or {}).get("id")
    if external_id and home_id == external_id:
        scored, conceded = home, away
    elif external_id and (match.get("awayTeam") or {}).get("id") == external_id:
        scored, conceded = away, home
    elif _normalize_name(home_name) in {_normalize_name(v) for v in names_for_matching(team_name, external_id)} or any(
        v in _normalize_name(home_name) or _normalize_name(home_name) in v
        for v in names_for_matching(team_name, external_id)
    ):
        scored, conceded = home, away
    else:
        scored, conceded = away, home

    return {
        "date": _format_date(match),
        "result": f"{home_name} {home}×{away} {away_name}",
        "scored": scored,
        "conceded": conceded,
        "line": f"{_format_date(match)} — {home_name} {home}×{away} {away_name} ({scored} mar, {conceded} lev)",
    }


def build_matches_snapshot(
    matches: list[dict],
    team_name: str,
    external_id: int | None = None,
    *,
    limit: int = 3,
) -> list[dict]:
    """Últimos jogos da seleção (mais recente primeiro)."""
    rows: list[tuple[datetime, dict]] = []
    seen: set[str] = set()
    for match in matches:
        snap = snapshot_match(match, team_name, external_id)
        if not snap:
            continue
        key = snap["result"]
        if key in seen:
            continue
        seen.add(key)
        rows.append((_parse_match_date(match) or datetime.min, snap))

    rows.sort(key=lambda item: item[0], reverse=True)
    return [snap for _, snap in rows[:limit]]

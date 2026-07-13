"""Portão de responsabilidade: sem base real = sem palpite (homologado ou alternativo)."""

from __future__ import annotations

import json
from typing import Any

# Fontes aceitas para publicar/compartilhar palpite
SOLID_SOURCES = frozenset({"api", "api+db", "db", "hybrid", "web_research"})

# Fontes que NUNCA geram palpite público
PROVISIONAL_MARKERS = frozenset(
    {
        "odds_implied",
        "odds",
        "provisional",
        "club_provisional",
        "synthetic",
        "inferred",
    }
)


def _raw_from_profile(profile: Any) -> dict:
    raw = getattr(profile, "raw_json", None)
    if not raw:
        return {}
    if isinstance(raw, dict):
        return raw
    try:
        return json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return {}


def profile_is_provisional(profile: Any) -> bool:
    raw = _raw_from_profile(profile)
    source = str(raw.get("source") or "").lower().strip()
    kind = str(raw.get("kind") or "").lower().strip()
    blob = f"{source} {kind}"
    if any(m in blob for m in PROVISIONAL_MARKERS):
        return True
    if source and source not in SOLID_SOURCES:
        # Fonte desconhecida → tratar como não publicável
        return True
    return False


def profile_has_solid_foundation(
    profile: Any,
    *,
    min_matches: int,
) -> tuple[bool, str]:
    """
    Retorna (ok, motivo).
    ok=False → produto NÃO emite best_pick (nem alternativo).
    """
    if profile is None:
        return False, "perfil estatístico ausente"
    sampled = int(getattr(profile, "matches_sampled", 0) or 0)
    if sampled < min_matches:
        return False, f"amostra insuficiente ({sampled}/{min_matches} jogos reais)"
    if profile_is_provisional(profile):
        raw = _raw_from_profile(profile)
        src = raw.get("source") or raw.get("kind") or "provisório"
        return (
            False,
            f"perfil sem fundamento histórico real (fonte: {src}) — sem palpite público",
        )
    return True, "ok"


def both_profiles_solid(
    home_profile: Any,
    away_profile: Any,
    *,
    min_matches: int,
) -> tuple[bool, list[str]]:
    reasons: list[str] = []
    ok_h, reason_h = profile_has_solid_foundation(home_profile, min_matches=min_matches)
    ok_a, reason_a = profile_has_solid_foundation(away_profile, min_matches=min_matches)
    if not ok_h:
        reasons.append(f"mandante: {reason_h}")
    if not ok_a:
        reasons.append(f"visitante: {reason_a}")
    return ok_h and ok_a, reasons

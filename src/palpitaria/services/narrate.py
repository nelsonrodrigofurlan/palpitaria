"""Narrativa LLM única — cartão + comentário curto. Modelo já decidiu o pick."""

from __future__ import annotations

import json
from typing import Any

from palpitaria.config import settings
from palpitaria.services.analyzer import FixtureAnalysis
from palpitaria.services.knockout_climate import is_knockout_stage, llm_knockout_suffix
from palpitaria.services.llm_client import chat_completion
from palpitaria.services.llm_utils import _parse_json_from_llm
from palpitaria.services.strategy_card import (
    _fallback_strategy_card,
    _finalize_card,
    compute_card_display_mode,
)

NARRATE_SYSTEM = """Você é o narrador da Palpitaria FC. O MODELO JÁ DECIDIU o mercado (best_pick).
Você NÃO muda o mercado principal. Só monta cartão exchange + 1 parágrafo de bastidores.

Retorne SOMENTE JSON:
{
  "headline": "síntese do cenário",
  "strategies": [
    {"label": "Principal", "market": "...", "side": "BACK", "thesis": "1-2 frases", "risk": "médio", "odds_hint": "—"}
  ],
  "avoid": "opcional",
  "comment": "1 parágrafo em português, máx 500 caracteres, sem markdown"
}

Regras:
- strategies: 2 ou 3; a Principal deve refletir best_pick.market
- comment: não repita odds/mercados em lista; tom analítico
- Não invente jogadores fora de home_insights/away_insights
"""


def _fallback_comment(analysis: FixtureAnalysis) -> str:
    pick = analysis.best_pick or {}
    pred = analysis.prediction or {}
    probs = pred.get("probs") or {}
    bit = pick.get("reason") or "Leitura numérica do modelo."
    extra = ""
    if probs.get("over_15") is not None:
        extra = f" P(Over 1.5) modelo={float(probs['over_15']):.0%}."
    return (
        f"{analysis.home_name} x {analysis.away_name} — {bit}{extra} "
        "Entradas no cartão acima; bastidores na grade."
    )[:500]


def narrate_fixture(
    analysis: FixtureAnalysis,
    *,
    odds: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], str]:
    """
    Uma chamada LLM → (strategy_card, llm_explanation).
    Sem LLM: fallback heurístico + comentário curto.
    """
    if not settings.has_llm:
        card = _fallback_strategy_card(analysis, odds=odds)
        return card, _fallback_comment(analysis)

    payload = {
        "match": f"{analysis.home_name} x {analysis.away_name}",
        "competition": (analysis.prediction or {}).get("competition_code"),
        "stage": analysis.stage,
        "is_knockout": getattr(analysis, "is_knockout", False),
        "excluded": analysis.excluded,
        "best_pick": analysis.best_pick,
        "prediction": analysis.prediction,
        "home_insights": analysis.home_insights,
        "away_insights": analysis.away_insights,
        "match_context": analysis.match_context,
        "odds": odds,
        "display_hint": compute_card_display_mode(analysis, odds),
    }

    system = NARRATE_SYSTEM
    if getattr(analysis, "is_knockout", False) or is_knockout_stage(analysis.stage):
        system += llm_knockout_suffix()

    try:
        raw = chat_completion(
            system,
            f"Dados:\n{json.dumps(payload, ensure_ascii=False, indent=2)}",
            temperature=0.3,
            max_tokens=900,
            feature="narrate",
        )
        parsed = _parse_json_from_llm(raw) or {}
        strategies = parsed.get("strategies") or []
        if not strategies:
            card = _fallback_strategy_card(analysis, odds=odds)
        else:
            card = _finalize_card(
                {
                    "headline": (parsed.get("headline") or "").strip()
                    or _fallback_strategy_card(analysis, odds=odds)["headline"],
                    "strategies": strategies[:3],
                    "avoid": parsed.get("avoid"),
                },
                analysis,
                odds,
            )
        comment = (parsed.get("comment") or "").strip() or _fallback_comment(analysis)
        if len(comment) > 500:
            comment = comment[:497] + "…"
        return card, comment
    except Exception:
        return _fallback_strategy_card(analysis, odds=odds), _fallback_comment(analysis)

"""Cartão de estratégias estruturado (2–3 entradas) — inspirado em leitura objetiva tipo exchange."""

from __future__ import annotations

import json
from typing import Any

from palpitaria.config import settings
from palpitaria.services.analyzer import FixtureAnalysis
from palpitaria.services.llm_client import chat_completion
from palpitaria.services.llm_utils import _parse_json_from_llm

STRATEGY_SYSTEM = """Você monta o CARTÃO DE ESTRATÉGIAS da Palpitaria FC para UM jogo do dia.

Formato: objetivo, escaneável, em português do Brasil — como analista de exchange (handicap, gols, 1X2).

Regras:
- 2 ou 3 estratégias ordenadas por prioridade (principal → alternativa → conservadora).
- Integre stats, bastidores, contexto, palpite oficial (best_pick) e odds quando existirem.
- Cite mercado concreto (ex.: "Over 1.5", "Handicap EUA -1", "Vitória EUA", "Lay CS 0-0").
- risk: "baixo" | "médio" | "alto"
- side: "BACK" | "LAY" | "NEUTRO"
- Não invente jogadores, placares ou odds fora do pacote.
- Se favorito com odd ML < 1.50, sugira handicap ou gols em vez de ML espremido.
- avoid: 1 frase sobre o que evitar (opcional).

Retorne SOMENTE JSON válido:
{
  "headline": "frase síntese do cenário",
  "strategies": [
    {
      "label": "Principal",
      "market": "nome do mercado",
      "side": "BACK",
      "thesis": "2 frases máximo",
      "risk": "médio",
      "odds_hint": "~2.10 ou —"
    }
  ],
  "avoid": "opcional — mercado/linha a evitar"
}
"""


def _fallback_strategy_card(analysis: FixtureAnalysis) -> dict[str, Any]:
    """Heurística sem LLM — garante cartão mínimo na UI."""
    pick = analysis.best_pick or {}
    market = pick.get("market") or "Sem palpite"
    strategies: list[dict[str, str]] = [
        {
            "label": "Oficial Palpitaria",
            "market": market,
            "side": "BACK",
            "thesis": (pick.get("reason") or "Leitura numérica do pipeline.")[:220],
            "risk": "médio" if analysis.excluded else "baixo",
            "odds_hint": "—",
        }
    ]
    if analysis.excluded and market.startswith("VITÓRIA"):
        strategies.append(
            {
                "label": "Trader",
                "market": "Handicap favorito -1 (se dominar)",
                "side": "BACK",
                "thesis": "Favorito esmagado: margem de gols pode pagar mais que ML.",
                "risk": "médio",
                "odds_hint": "—",
            }
        )
    headline = (
        f"Fora do filtro de gols — foco em alternativa ({market})."
        if analysis.excluded
        else f"Candidato a gols — score {analysis.goal_potential_score:.0f}%."
    )
    return {"headline": headline, "strategies": strategies[:3], "avoid": None}


def build_strategy_card(
    analysis: FixtureAnalysis,
    *,
    odds: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if not settings.has_llm:
        return _fallback_strategy_card(analysis)

    payload = {
        "match": f"{analysis.home_name} x {analysis.away_name}",
        "excluded": analysis.excluded,
        "exclusion_reasons": analysis.exclusion_reasons,
        "goal_potential_score": analysis.goal_potential_score,
        "best_pick": analysis.best_pick,
        "criteria_brief": analysis.criteria_brief,
        "home_stats": analysis.home_stats_meta,
        "away_stats": analysis.away_stats_meta,
        "home_insights": analysis.home_insights,
        "away_insights": analysis.away_insights,
        "match_context": analysis.match_context,
        "odds": odds,
    }

    try:
        user_content = f"Dados do jogo:\n{json.dumps(payload, ensure_ascii=False, indent=2)}"
        response = chat_completion(
            STRATEGY_SYSTEM,
            user_content,
            temperature=0.3,
            max_tokens=700,
            feature="strategy_card",
        )
        parsed = _parse_json_from_llm(response)
        if not parsed or not parsed.get("strategies"):
            return _fallback_strategy_card(analysis)
        strategies = parsed.get("strategies") or []
        return {
            "headline": (parsed.get("headline") or "").strip() or _fallback_strategy_card(analysis)["headline"],
            "strategies": strategies[:3],
            "avoid": parsed.get("avoid"),
        }
    except Exception:
        return _fallback_strategy_card(analysis)

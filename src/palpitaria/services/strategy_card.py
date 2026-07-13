"""Cartão de estratégias estruturado (2–3 entradas) — inspirado em leitura objetiva tipo exchange."""

from __future__ import annotations

import json
import re
from typing import Any

from palpitaria.config import settings
from palpitaria.services.analyzer import FixtureAnalysis
from palpitaria.services.knockout_climate import is_knockout_stage, llm_knockout_suffix
from palpitaria.services.llm_client import chat_completion
from palpitaria.services.llm_utils import _parse_json_from_llm

FAVORITE_ML_SQUEEZE = 1.50

STRATEGY_SYSTEM = """Você monta o CARTÃO DE ESTRATÉGIAS da Palpitaria FC para UM jogo do dia.

Leitura de EXCHANGE (Betfair): objetivo, escaneável, português do Brasil.

Prioridade de mercado:
1. Se odds.ML do favorito < 1.50 → NÃO empurre vitória seca; priorize handicap asiático (-1 / +2) ou gols (Over).
2. Jogo homologado no filtro de gols → Principal em Over (1.5 ou 2.5 conforme números); alternativa pode ser handicap.
3. Jogo fora do filtro (excluded) → Principal em handicap do favorito ou lay de placar baixo; vitória seca só como Conservadora.
4. Cite odds reais do pacote (odds.lines) em odds_hint — ex.: "@ 1.72" ou "Over 2.5 @ 2.10". Se não houver linha, use "—".

Labels obrigatórios (use exatamente um por estratégia):
- "Principal" | "Conservadora" | "Trader" | "Evitar" (só se couber como linha de mercado a evitar)

Regras:
- 2 ou 3 estratégias ordenadas por prioridade.
- Integre stats, bastidores, contexto, best_pick e odds.lines.
- Mercado concreto: "Over 1.5", "Handicap Inglaterra -1", "Lay CS 0-0", etc.
- risk: "baixo" | "médio" | "alto"
- side: "BACK" | "LAY" | "NEUTRO"
- thesis: no máximo 2 frases curtas.
- Não invente jogadores, placares ou odds fora do pacote.
- avoid: 1 frase sobre mercado/linha a evitar (opcional, fora do array strategies).

Retorne SOMENTE JSON válido:
{
  "headline": "frase síntese do cenário exchange",
  "strategies": [
    {
      "label": "Principal",
      "market": "nome do mercado",
      "side": "BACK",
      "thesis": "2 frases máximo",
      "risk": "médio",
      "odds_hint": "@ 1.85 ou —"
    }
  ],
  "avoid": "opcional"
}
"""


def _norm(name: str) -> str:
    return re.sub(r"\s+", " ", (name or "").lower().strip())


def _favorite_ml_price(
    odds: dict[str, Any] | None,
    home_name: str,
    away_name: str,
) -> float | None:
    if not odds:
        return None
    h2h = next((m for m in (odds.get("markets") or []) if m.get("key") == "h2h"), None)
    if not h2h:
        return None
    home_n, away_n = _norm(home_name), _norm(away_name)
    home_price = away_price = None
    for outcome in h2h.get("outcomes") or []:
        name = _norm(str(outcome.get("name") or ""))
        price = outcome.get("price")
        if price is None:
            continue
        try:
            p = float(price)
        except (TypeError, ValueError):
            continue
        if name == home_n:
            home_price = p
        elif name == away_n:
            away_price = p
    candidates = [p for p in (home_price, away_price) if p is not None]
    return min(candidates) if candidates else None


def _odds_summary_lines(odds: dict[str, Any] | None) -> list[str]:
    if not odds:
        return []
    lines: list[str] = []
    for mkt in odds.get("markets") or []:
        key = mkt.get("key") or ""
        outcomes = mkt.get("outcomes") or []
        if key == "h2h":
            parts = [f"{o.get('name')} @ {o.get('price')}" for o in outcomes if o.get("price")]
            if parts:
                lines.append(f"1X2: {', '.join(parts)}")
        elif key == "totals":
            parts = []
            for o in outcomes:
                point = o.get("point")
                label = f"{o.get('name')} {point}".strip() if point is not None else str(o.get("name") or "")
                if o.get("price"):
                    parts.append(f"{label} @ {o.get('price')}")
            if parts:
                lines.append(f"Gols: {', '.join(parts)}")
        elif key in ("spreads", "alternate_spreads"):
            parts = []
            for o in outcomes:
                point = o.get("point")
                label = f"{o.get('name')} {point:+g}" if point is not None else str(o.get("name") or "")
                if o.get("price"):
                    parts.append(f"{label} @ {o.get('price')}")
            if parts:
                lines.append(f"Handicap: {', '.join(parts)}")
    return lines


def compute_card_display_mode(
    analysis: FixtureAnalysis,
    odds: dict[str, Any] | None = None,
) -> str:
    """goals_primary = destaque Over; handicap_primary = destaque cartão handicap."""
    if analysis.excluded:
        return "handicap_primary"
    fav_ml = _favorite_ml_price(odds, analysis.home_name, analysis.away_name)
    if fav_ml is not None and fav_ml < FAVORITE_ML_SQUEEZE:
        return "handicap_primary"
    pick = analysis.best_pick or {}
    market = (pick.get("market") or "").upper()
    if "OVER" in market or "UNDER" in market:
        return "goals_primary"
    if not analysis.excluded:
        return "goals_primary"
    return "handicap_primary"


def enrich_strategy_card_display_mode(
    analysis: FixtureAnalysis,
    odds: dict[str, Any] | None = None,
) -> None:
    mode = compute_card_display_mode(analysis, odds)
    if analysis.strategy_card:
        analysis.strategy_card["display_mode"] = mode
    else:
        analysis.strategy_card = {"display_mode": mode, "strategies": []}


def _finalize_card(
    card: dict[str, Any],
    analysis: FixtureAnalysis,
    odds: dict[str, Any] | None,
) -> dict[str, Any]:
    card["display_mode"] = compute_card_display_mode(analysis, odds)
    return card


def _infer_favorite_team(analysis: FixtureAnalysis) -> str | None:
    pick = analysis.best_pick or {}
    market = pick.get("market") or ""
    if market.startswith("VITÓRIA:"):
        return market.split(":", 1)[1].strip()
    home_meta = analysis.home_stats_meta or {}
    away_meta = analysis.away_stats_meta or {}
    h_g = home_meta.get("avg_goals_scored") or home_meta.get("goals_scored_avg")
    a_g = away_meta.get("avg_goals_scored") or away_meta.get("goals_scored_avg")
    if h_g is not None and a_g is not None:
        try:
            if float(h_g) > float(a_g) + 0.15:
                return analysis.home_name
            if float(a_g) > float(h_g) + 0.15:
                return analysis.away_name
        except (TypeError, ValueError):
            pass
    return analysis.home_name


def _fallback_strategy_card(
    analysis: FixtureAnalysis,
    *,
    odds: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Heurística sem LLM — garante cartão mínimo na UI."""
    pick = analysis.best_pick or {}
    market = pick.get("market") or "Sem palpite"
    fav = _infer_favorite_team(analysis)
    fav_ml = _favorite_ml_price(odds, analysis.home_name, analysis.away_name)
    ml_note = f" ML favorito ~{fav_ml:.2f}" if fav_ml else ""

    strategies: list[dict[str, str]] = []
    if analysis.excluded or (fav_ml is not None and fav_ml < FAVORITE_ML_SQUEEZE):
        strategies.append(
            {
                "label": "Principal",
                "market": f"Handicap {fav} -1" if fav else "Handicap favorito -1",
                "side": "BACK",
                "thesis": (
                    f"Favorito esmagado{ml_note}: margem de gols paga melhor que vitória seca."
                    if fav_ml and fav_ml < FAVORITE_ML_SQUEEZE
                    else "Jogo fora do filtro de gols — buscar valor em margem, não em ML."
                )[:220],
                "risk": "médio",
                "odds_hint": "—",
            }
        )
        if market and not market.startswith("Handicap"):
            strategies.append(
                {
                    "label": "Conservadora",
                    "market": market,
                    "side": "BACK",
                    "thesis": (pick.get("reason") or "Palpite alternativo do pipeline.")[:220],
                    "risk": "médio",
                    "odds_hint": "—",
                }
            )
    else:
        over_market = market if "OVER" in market.upper() and "2.5" not in market.upper() else "Over 1.5"
        if analysis.excluded is False and getattr(analysis, "is_knockout", False):
            over_market = "Over 1.5"
        strategies.append(
            {
                "label": "Principal",
                "market": over_market,
                "side": "BACK",
                "thesis": (pick.get("reason") or f"Candidato a gols — score {analysis.goal_potential_score:.0f}%.")[:220],
                "risk": "baixo",
                "odds_hint": "—",
            }
        )
        if fav:
            strategies.append(
                {
                    "label": "Trader",
                    "market": f"Handicap {fav} -1",
                    "side": "BACK",
                    "thesis": "Se dominar o jogo, linha -1 pode superar o Over em odd.",
                    "risk": "médio",
                    "odds_hint": "—",
                }
            )

    if not strategies:
        strategies.append(
            {
                "label": "Principal",
                "market": market,
                "side": "BACK",
                "thesis": (pick.get("reason") or "Leitura numérica do pipeline.")[:220],
                "risk": "médio",
                "odds_hint": "—",
            }
        )

    headline = (
        f"Exchange: favorito espremido{ml_note} — foco handicap/gols, não ML."
        if fav_ml and fav_ml < FAVORITE_ML_SQUEEZE
        else (
            f"Fora do filtro de gols — leitura alternativa ({market})."
            if analysis.excluded
            else f"Candidato a gols — score {analysis.goal_potential_score:.0f}%."
        )
    )
    avoid = "Vitória seca do favorito" if fav_ml and fav_ml < FAVORITE_ML_SQUEEZE else None
    return _finalize_card(
        {"headline": headline, "strategies": strategies[:3], "avoid": avoid},
        analysis,
        odds,
    )


def build_strategy_card(
    analysis: FixtureAnalysis,
    *,
    odds: dict[str, Any] | None = None,
) -> dict[str, Any]:
    odds_lines = _odds_summary_lines(odds)
    fav_ml = _favorite_ml_price(odds, analysis.home_name, analysis.away_name)

    if not settings.has_llm:
        return _fallback_strategy_card(analysis, odds=odds)

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
        "is_knockout": getattr(analysis, "is_knockout", False),
        "stage": analysis.stage,
        "odds": {
            "favorite_ml": fav_ml,
            "lines": odds_lines,
            "raw": odds,
        },
        "display_hint": compute_card_display_mode(analysis, odds),
    }

    try:
        system = STRATEGY_SYSTEM
        if getattr(analysis, "is_knockout", False) or is_knockout_stage(analysis.stage):
            system += llm_knockout_suffix()
            system += """
Em mata-mata, inclua no cartão (se couber):
- Uma linha "Trader" com leitura LIVE (0-0 HT, favorito abre, zebra abre) usando knockout_live_scenarios do payload.
- Evite Principal em Over 2.5 pré-live salvo perfil extremo no payload.
"""
        user_content = f"Dados do jogo:\n{json.dumps(payload, ensure_ascii=False, indent=2)}"
        response = chat_completion(
            system,
            user_content,
            temperature=0.3,
            max_tokens=700,
            feature="strategy_card",
        )
        parsed = _parse_json_from_llm(response)
        if not parsed or not parsed.get("strategies"):
            return _fallback_strategy_card(analysis, odds=odds)
        strategies = parsed.get("strategies") or []
        card = {
            "headline": (parsed.get("headline") or "").strip()
            or _fallback_strategy_card(analysis, odds=odds)["headline"],
            "strategies": strategies[:3],
            "avoid": parsed.get("avoid"),
        }
        return _finalize_card(card, analysis, odds)
    except Exception:
        return _fallback_strategy_card(analysis, odds=odds)

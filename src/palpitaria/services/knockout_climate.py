"""Inteligência de clima tático em jogos de mata-mata (qualquer campeonato)."""

from __future__ import annotations

from typing import Any

# Thresholds mais exigentes para homologar Over 2.5 em eliminatória
KO_OVER25_MIN_COMBINED_AVG = 3.85
KO_OVER25_MIN_RATE = 0.68
KO_OVER25_MIN_CONFIDENCE = 98.0

KNOCKOUT_CLIMATE_BRIEF = (
    "Mata-mata: futebol atual é mais físico que técnico; raramente é jogo aberto de saída. "
    "O time mais fraco fecha, recua os 11 e o favorito não ganha com tranquilidade — "
    "especialmente no 1º tempo. 2º tempo em 0-0 costuma seguir truncado. "
    "Se alguém abre o placar, a partida vira outra: favorito na frente tende a jogo elástico; "
    "zebra na frente trava ainda mais atrás da bola. Pré-live: priorize linhas conservadoras "
    "(Over 1.5, handicap, live); Over 2.5 só com perfil extremamente aberto."
)

KNOCKOUT_LIVE_SCENARIOS = [
    {
        "scoreline": "0-0 HT",
        "reading": "Cenário base: jogo fechado, favorito sem espaço fácil.",
        "lean": "Evitar Over alto pré-live; considerar Under 1.5 HT ou esperar live.",
    },
    {
        "scoreline": "0-0 2º tempo",
        "reading": "Ainda truncado; favorito pressiona mas underdog segura bloco.",
        "lean": "Over tardio ou lay de empate/Under — não forçar Over 2.5 pré.",
    },
    {
        "scoreline": "Favorito abre",
        "reading": "Placar elástico ganha probabilidade; defesa abre para empatar.",
        "lean": "Over live, próximo gol, handicap -1 do favorito.",
    },
    {
        "scoreline": "Underdog abre",
        "reading": "Time fraco recua os 11; jogo pode fechar de novo ou virar trocação se favorito vai tudo.",
        "lean": "Lay zebra, Under reativo ou vitória favorito com jogo feio.",
    },
]


def is_knockout_stage(stage: str | None) -> bool:
    """True para fases eliminatórias (football-data.org e equivalentes)."""
    if not stage or not str(stage).strip():
        return False
    s = str(stage).upper().replace("-", "_").replace(" ", "_")
    if "GROUP" in s:
        return False
    markers = (
        "LAST_16",
        "ROUND_OF_16",
        "ROUND_OF16",
        "QUARTER",
        "SEMI",
        "FINAL",
        "THIRD",
        "PLAY_OFF",
        "PLAYOFF",
        "KNOCKOUT",
        "ELIMIN",
        "OITAVAS",
        "QUARTAS",
    )
    return any(m in s for m in markers)


def knockout_intel_payload() -> dict[str, Any]:
    """Pacote para LLM, cartão de estratégias e contexto persistido."""
    return {
        "knockout": True,
        "knockout_climate": KNOCKOUT_CLIMATE_BRIEF,
        "knockout_live_scenarios": KNOCKOUT_LIVE_SCENARIOS,
        "knockout_pre_live_bias": (
            "Prefira Over 1.5, handicap asiático ou leitura live. "
            "Desconfie de Over 2.5 baseado só em médias da fase anterior."
        ),
    }


def enrich_match_context_knockout(
    match_context: dict[str, Any] | None,
    *,
    stage: str | None,
) -> dict[str, Any]:
    ctx = dict(match_context or {})
    if not is_knockout_stage(stage):
        return ctx
    ctx.update(knockout_intel_payload())
    return ctx


def enrich_analysis_knockout(analysis) -> None:
    """Anexa inteligência de mata-mata ao contexto e ao criteria_brief."""
    if not getattr(analysis, "is_knockout", False) and not is_knockout_stage(
        getattr(analysis, "stage", None)
    ):
        return
    analysis.is_knockout = True
    analysis.match_context = enrich_match_context_knockout(
        analysis.match_context,
        stage=analysis.stage,
    )
    brief = dict(analysis.criteria_brief or {})
    brief["knockout_climate"] = KNOCKOUT_CLIMATE_BRIEF
    analysis.criteria_brief = brief


def knockout_over25_thresholds() -> tuple[float, float, float]:
    """(combined_avg_min, over_25_rate_min, confidence_min)."""
    return KO_OVER25_MIN_COMBINED_AVG, KO_OVER25_MIN_RATE, KO_OVER25_MIN_CONFIDENCE


def adjust_best_pick_for_knockout(
    pick: dict[str, Any] | None,
    *,
    stage: str | None,
) -> dict[str, Any] | None:
    """
    Em mata-mata, não homologa Over 2.5 como palpite principal:
    rebaixa para Over 1.5 com nota tática.
    """
    if not pick or not is_knockout_stage(stage):
        return pick
    market = (pick.get("market") or "").upper()
    if "OVER 2.5" not in market:
        return pick
    adjusted = dict(pick)
    adjusted["market"] = "OVER 1.5 GOALS"
    adjusted["verdict"] = "CANDIDATE" if pick.get("verdict") == "STRONG" else pick.get("verdict")
    ko_note = (
        "Ajuste mata-mata: eliminatória tende a 1º tempo fechado; Over 2.5 exige abertura após gol. "
        "Linha pré-live mais coerente: Over 1.5 (ou trader live se o placar abrir)."
    )
    prior = (pick.get("reason") or "").strip()
    adjusted["reason"] = f"{prior} {ko_note}".strip()[:520]
    adjusted["knockout_adjusted_from"] = "OVER 2.5 GOALS"
    adjusted["scope"] = pick.get("scope", "goals")
    return adjusted


def llm_knockout_suffix() -> str:
    return f"""

MATA-MATA (obrigatório neste jogo):
{KNOCKOUT_CLIMATE_BRIEF}
- Não trate stats de fase anterior como jogo aberto garantido.
- Pré-live: evite empurrar Over 2.5; prefira Over 1.5, handicap ou cenários live descritos no contexto.
- Mencione que 0-0 no 2º tempo ainda é cenário provável até abrir o placar.
"""

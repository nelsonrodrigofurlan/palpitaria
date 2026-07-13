"""analisar_jogos_hoje — Poisson + foundation; LLM narra só se houver pick."""

from __future__ import annotations

from typing import Any

from palpitaria.database import SessionLocal
from palpitaria.services.analyzer import (
    analyze_upcoming,
    default_match_context,
    get_today_context,
    persist_analysis,
)
from palpitaria.services.knockout_climate import enrich_analysis_knockout


def _is_foundation_block(analysis) -> bool:
    pred = analysis.prediction or {}
    if pred.get("reason") == "foundation_gate" or pred.get("blocked"):
        return True
    blob = " ".join(analysis.exclusion_reasons or []).lower()
    return "fundamento" in blob or "provisória" in blob or "provisoria" in blob


def _pick_row(analysis) -> dict[str, Any]:
    pick = analysis.best_pick or {}
    return {
        "jogo": f"{analysis.home_name} x {analysis.away_name}",
        "fixture_id": analysis.fixture_id,
        "mercado": pick.get("market"),
        "verdict": pick.get("verdict"),
        "scope": pick.get("scope"),
        "edge": pick.get("edge") or (analysis.prediction or {}).get("edge"),
        "score": analysis.goal_potential_score,
        "excluded": analysis.excluded,
        "motivos": list(analysis.exclusion_reasons or []),
    }


def analisar_jogos_hoje(
    competicoes: list[str] | None = None,
    limite: int = 50,
    *,
    narrar: bool = True,
    persistir: bool = True,
) -> dict[str, Any]:
    codes = [c.strip().upper() for c in (competicoes or ["BSA", "BSB"]) if c]
    ctx = get_today_context()
    db = SessionLocal()
    homologadas: list[dict[str, Any]] = []
    alternativas: list[dict[str, Any]] = []
    descartes: list[dict[str, Any]] = []
    sem_fundamento: list[dict[str, Any]] = []

    try:
        for code in codes:
            analyses = analyze_upcoming(
                db,
                limit=limite,
                for_today_only=True,
                competition_code=code,
            )
            for analysis in analyses:
                enrich_analysis_knockout(analysis)
                if not analysis.match_context:
                    analysis.match_context = default_match_context()

                if _is_foundation_block(analysis):
                    row = _pick_row(analysis)
                    row["motivo"] = "; ".join(analysis.exclusion_reasons or ["sem fundamento"])
                    sem_fundamento.append(row)
                    if persistir:
                        persist_analysis(db, analysis, "", competition_code=code)
                    continue

                if analysis.best_pick is None:
                    row = _pick_row(analysis)
                    row["motivo"] = "; ".join(analysis.exclusion_reasons or ["descarte sem pick"])
                    descartes.append(row)
                    if persistir:
                        persist_analysis(db, analysis, "", competition_code=code)
                    continue

                if narrar:
                    try:
                        from palpitaria.services.chat_service import _odds_for_match
                        from palpitaria.services.narrate import narrate_fixture

                        odds = _odds_for_match(db, analysis.home_name, analysis.away_name, code)
                        card, comment = narrate_fixture(analysis, odds=odds)
                        analysis.strategy_card = card
                        analysis.llm_explanation = comment
                    except Exception as exc:  # noqa: BLE001 — narração não bloqueia classificação
                        analysis.llm_explanation = f"(narrativa indisponível: {exc})"

                if persistir:
                    persist_analysis(
                        db,
                        analysis,
                        analysis.llm_explanation or "",
                        competition_code=code,
                    )

                row = _pick_row(analysis)
                if analysis.excluded:
                    alternativas.append(row)
                else:
                    homologadas.append(row)

        total = len(homologadas) + len(alternativas) + len(descartes) + len(sem_fundamento)
        return {
            "total": total,
            "dia_label": ctx.label,
            "homologadas": homologadas,
            "alternativas": alternativas,
            "descartes": descartes,
            "sem_fundamento": sem_fundamento,
        }
    finally:
        db.close()

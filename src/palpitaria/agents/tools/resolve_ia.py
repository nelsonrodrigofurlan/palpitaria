"""resolver_historico_ia — liquida PENDING com placar final."""

from __future__ import annotations

from typing import Any

from palpitaria.database import SessionLocal
from palpitaria.models import AiRecommendation
from palpitaria.services.ai_tracker import resolve_pending_recommendations


def resolver_historico_ia(competicao: str | None = None) -> dict[str, Any]:
    code = (competicao or "").strip().upper() or None
    db = SessionLocal()
    try:
        pending_q = db.query(AiRecommendation).filter(AiRecommendation.outcome == "PENDING")
        if code:
            pending_q = pending_q.filter(AiRecommendation.competition_code == code)
        pendentes_antes = pending_q.count()

        resolvidos = resolve_pending_recommendations(db, code)

        remaining_q = db.query(AiRecommendation).filter(AiRecommendation.outcome == "PENDING")
        if code:
            remaining_q = remaining_q.filter(AiRecommendation.competition_code == code)
        pendentes = remaining_q.count()

        hits_q = db.query(AiRecommendation).filter(AiRecommendation.outcome == "HIT")
        misses_q = db.query(AiRecommendation).filter(AiRecommendation.outcome == "MISS")
        if code:
            hits_q = hits_q.filter(AiRecommendation.competition_code == code)
            misses_q = misses_q.filter(AiRecommendation.competition_code == code)

        return {
            "resolvidos": resolvidos,
            "pendentes_antes": pendentes_antes,
            "pendentes": pendentes,
            "hits": hits_q.count(),
            "misses": misses_q.count(),
            "competicao": code,
        }
    finally:
        db.close()

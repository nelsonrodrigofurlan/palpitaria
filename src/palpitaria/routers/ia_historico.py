"""Histórico das recomendações da IA (homologadas x alternativas)."""

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from palpitaria.config import settings
from palpitaria.database import get_db
from palpitaria.deps import TEMPLATES, login_required
from palpitaria.models import AiRecommendation, Competition
from palpitaria.services.ai_tracker import (
    build_month_options,
    compute_split_stats,
    ensure_ia_history_from_reports,
    filter_recommendations_by_month,
    market_rows_from_stats,
    parse_month_param,
    prune_discarded_pending_recommendations,
    resolve_pending_recommendations,
    rows_for_scope,
)
from palpitaria.services.ledger import current_period, period_label

router = APIRouter()


@router.get("/ia-historico", response_class=HTMLResponse)
def list_ia_historico(
    request: Request,
    comp: str | None = None,
    mes: str | None = None,
    db: Session = Depends(get_db),
    user=Depends(login_required),
):
    active_comps = db.query(Competition).filter_by(is_active=True).order_by(Competition.code).all()
    # Default Todos (como /graficos)
    comp_code = (comp or "").strip().upper() or None
    if comp_code == "ALL":
        comp_code = None

    resolve_pending_recommendations(db, comp_code)
    prune_discarded_pending_recommendations(db, comp_code)

    query = db.query(AiRecommendation).order_by(AiRecommendation.analyzed_at.desc())
    if comp_code:
        query = query.filter(AiRecommendation.competition_code == comp_code)
    all_recommendations = query.limit(500).all()

    month_options = build_month_options(all_recommendations)
    year, month = parse_month_param(mes)
    selected_mes = f"{year}-{month:02d}"
    selected_period = period_label(year, month)

    filtered = filter_recommendations_by_month(all_recommendations, year, month)
    ensure_ia_history_from_reports(db, comp_code, year, month)
    # Recarregar após possível backfill
    reload_q = db.query(AiRecommendation).order_by(AiRecommendation.analyzed_at.desc())
    if comp_code:
        reload_q = reload_q.filter(AiRecommendation.competition_code == comp_code)
    all_recommendations = reload_q.limit(500).all()
    filtered = filter_recommendations_by_month(all_recommendations, year, month)
    split = compute_split_stats(filtered)

    cy, cm = current_period()
    return TEMPLATES.TemplateResponse(
        request,
        "ia_historico.html",
        {
            "homologated": split["homologated"],
            "alternate": split["alternate"],
            "homologated_market_rows": market_rows_from_stats(split["homologated"]),
            "alternate_market_rows": market_rows_from_stats(split["alternate"]),
            "homologated_rows": rows_for_scope(filtered, homologated=True),
            "alternate_rows": rows_for_scope(filtered, homologated=False),
            "month_options": month_options,
            "selected_mes": selected_mes,
            "selected_period": selected_period,
            "current_period": period_label(cy, cm),
            "app_timezone": settings.app_timezone,
            "active_comps": active_comps,
            "current_comp": comp_code or "",
            "user": user,
        },
    )

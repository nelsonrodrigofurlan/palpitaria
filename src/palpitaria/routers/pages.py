"""Páginas estáticas/institucionais (sobre, gestão de banca, aviso legal)."""

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse

from palpitaria.config import settings
from palpitaria.deps import TEMPLATES, login_required
from palpitaria.services.ledger import current_period, period_label

router = APIRouter()


@router.get("/sobre", response_class=HTMLResponse)
def about_page(request: Request, user=Depends(login_required)):
    cy, cm = current_period()
    return TEMPLATES.TemplateResponse(
        request,
        "sobre.html",
        {
            "current_period": period_label(cy, cm),
            "app_timezone": settings.app_timezone,
        }
    )


@router.get("/leitura/gestao-de-banca", response_class=HTMLResponse)
def leitura_gestao_banca(request: Request, user=Depends(login_required)):
    cy, cm = current_period()
    return TEMPLATES.TemplateResponse(
        request,
        "leitura_gestao_banca.html",
        {
            "current_period": period_label(cy, cm),
            "app_timezone": settings.app_timezone,
        },
    )


@router.get("/legal/aviso-legal", response_class=HTMLResponse)
def aviso_legal_page(request: Request):
    cy, cm = current_period()
    return TEMPLATES.TemplateResponse(
        request,
        "aviso_legal.html",
        {
            "current_period": period_label(cy, cm),
            "app_timezone": settings.app_timezone,
        },
    )

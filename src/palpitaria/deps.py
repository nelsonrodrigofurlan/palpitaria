"""Dependências e utilidades compartilhadas entre routers (auth, templates, filtros Jinja)."""

import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from fastapi import Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from palpitaria.database import get_db

TEMPLATES = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))


def _format_kickoff(utc_naive: datetime, tz_name: str) -> str:
    kickoff = utc_naive.replace(tzinfo=ZoneInfo("UTC")).astimezone(ZoneInfo(tz_name))
    return kickoff.strftime("%d/%m %H:%M")


TEMPLATES.env.filters["kickoff"] = _format_kickoff
TEMPLATES.env.filters["tojson"] = lambda obj: json.dumps(obj, ensure_ascii=False)


def hit_rate_pct(wins: int, total: int) -> int | None:
    """% de acerto no mês: greens ÷ total de entradas."""
    if total <= 0:
        return None
    return round(wins / total * 100)


def get_current_user(request: Request, db: Session = Depends(get_db)):
    user_id = request.session.get("user_id")
    if not user_id:
        return None
    from palpitaria.models import User
    return db.query(User).filter(User.id == user_id).first()


def login_required(request: Request, user=Depends(get_current_user)):
    if not user:
        if request.headers.get("HX-Request"):
            return HTMLResponse(headers={"HX-Redirect": "/login"})
        raise HTTPException(status_code=303, headers={"Location": "/login"})
    return user


def admin_required(request: Request, user=Depends(login_required)):
    if not getattr(user, "is_admin", False):
        raise HTTPException(status_code=403, detail="Acesso negado: peça beça pro Pai")
    return user

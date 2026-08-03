from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from palpitaria.database import get_db
from palpitaria.deps import TEMPLATES
from palpitaria.services.auth import get_user_by_email, verify_password

router = APIRouter()


@router.get("/login", response_class=HTMLResponse)
def login_page(request: Request):
    if request.session.get("user_id"):
        return RedirectResponse(url="/", status_code=303)
    return TEMPLATES.TemplateResponse(request, "login.html", {"error": None})


@router.post("/login", response_class=HTMLResponse)
async def login(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
    accept_terms: str | None = Form(None),
    db: Session = Depends(get_db),
):
    if accept_terms != "on":
        return TEMPLATES.TemplateResponse(
            request,
            "login.html",
            {"error": "É necessário aceitar o Aviso Legal e declarar ser maior de 18 anos."},
        )
    user = get_user_by_email(db, email)
    if not user or not verify_password(password, user.hashed_password):
        return TEMPLATES.TemplateResponse(request, "login.html", {"error": "E-mail ou senha inválidos."})

    request.session["user_id"] = user.id
    request.session["user_email"] = user.email
    request.session["is_admin"] = bool(user.is_admin)
    request.session["terms_accepted"] = True
    return RedirectResponse(url="/", status_code=303)


@router.get("/logout")
def logout(request: Request):
    request.session.clear()
    return RedirectResponse(url="/login", status_code=303)

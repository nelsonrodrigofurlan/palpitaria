"""Área administrativa: usuários, configuração de APIs/competições, custos, skills, fontes de scouting."""

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from palpitaria.config import settings
from palpitaria.database import get_db
from palpitaria.deps import TEMPLATES, admin_required
from palpitaria.models import ApiConfig, Competition, Team, User
from palpitaria.services.ledger import current_period, period_label

router = APIRouter()


@router.get("/admin/users", response_class=HTMLResponse)
def admin_users(request: Request, db: Session = Depends(get_db), user=Depends(admin_required)):
    users = db.query(User).order_by(User.created_at.desc()).all()
    cy, cm = current_period()
    return TEMPLATES.TemplateResponse(
        request,
        "admin_users.html",
        {
            "users": users,
            "current_period": period_label(cy, cm),
            "app_timezone": settings.app_timezone,
        }
    )


@router.post("/admin/users/add")
async def admin_add_user(request: Request, db: Session = Depends(get_db), user=Depends(admin_required)):
    from palpitaria.services.auth import get_password_hash

    form = await request.form()

    new_user = User(
        email=form.get("email"),
        full_name=form.get("full_name"),
        hashed_password=get_password_hash(form.get("password")),
        is_active=True
    )
    db.add(new_user)
    db.commit()
    return RedirectResponse(url="/admin/users", status_code=303)


@router.post("/admin/users/toggle/{target_id}")
def admin_toggle_user(target_id: int, db: Session = Depends(get_db), user=Depends(admin_required)):
    target = db.query(User).filter(User.id == target_id).first()
    if target and not target.is_admin:
        target.is_active = not target.is_active
        db.commit()
    return RedirectResponse(url="/admin/users", status_code=303)


@router.post("/admin/users/delete/{target_id}")
def admin_delete_user(target_id: int, db: Session = Depends(get_db), user=Depends(admin_required)):
    target = db.query(User).filter(User.id == target_id).first()
    if target and not target.is_admin:
        db.delete(target)
        db.commit()
    return RedirectResponse(url="/admin/users", status_code=303)


@router.get("/admin/config", response_class=HTMLResponse)
def admin_config(request: Request, db: Session = Depends(get_db), user=Depends(admin_required)):
    configs = db.query(ApiConfig).order_by(ApiConfig.key).all()
    competitions = db.query(Competition).order_by(Competition.is_active.desc(), Competition.name).all()
    cy, cm = current_period()
    return TEMPLATES.TemplateResponse(
        request,
        "admin_config.html",
        {
            "configs": configs,
            "competitions": competitions,
            "current_period": period_label(cy, cm),
            "app_timezone": settings.app_timezone,
        }
    )


@router.post("/admin/config/api/update")
async def admin_update_api_config(request: Request, db: Session = Depends(get_db), user=Depends(admin_required)):
    form = await request.form()
    key = form.get("key")
    value = form.get("value")

    cfg = db.query(ApiConfig).filter_by(key=key).first()
    if cfg:
        cfg.value = value
        db.commit()
    return RedirectResponse(url="/admin/config", status_code=303)


@router.post("/admin/config/competition/add")
async def admin_add_competition(request: Request, db: Session = Depends(get_db), user=Depends(admin_required)):
    form = await request.form()
    code = form.get("code").upper()
    name = form.get("name")
    season = int(form.get("season", 2026))

    if not db.query(Competition).filter_by(code=code).first():
        new_comp = Competition(code=code, name=name, season=season, is_active=True)
        db.add(new_comp)
        db.commit()
    return RedirectResponse(url="/admin/config", status_code=303)


@router.post("/admin/config/competition/toggle/{comp_id}")
def admin_toggle_competition(comp_id: int, db: Session = Depends(get_db), user=Depends(admin_required)):
    comp = db.query(Competition).filter_by(id=comp_id).first()
    if comp:
        comp.is_active = not comp.is_active
        db.commit()
    return RedirectResponse(url="/admin/config", status_code=303)


@router.get("/admin/custos", response_class=HTMLResponse)
def admin_custos(request: Request, db: Session = Depends(get_db), user=Depends(admin_required)):
    from palpitaria.services.cost_service import build_cost_dashboard

    dashboard = build_cost_dashboard(db)
    return TEMPLATES.TemplateResponse(
        request,
        "admin_custos.html",
        {
            **dashboard,
            "app_timezone": settings.app_timezone,
        },
    )


@router.get("/admin/skills", response_class=HTMLResponse)
def admin_skills(
    request: Request,
    doc: str | None = None,
    user=Depends(admin_required),
):
    from palpitaria.services.skills_reader import list_skill_docs, read_skill_doc

    skills = list_skill_docs()
    selected = read_skill_doc(doc) if doc else None
    return TEMPLATES.TemplateResponse(
        request,
        "admin_skills.html",
        {
            "skills": skills,
            "selected": selected,
            "app_timezone": settings.app_timezone,
        },
    )


@router.get("/admin/fontes", response_class=HTMLResponse)
def admin_fontes(request: Request, db: Session = Depends(get_db), user=Depends(admin_required)):
    from palpitaria.services.scouting_preferences import list_scouting_sources

    sources = list_scouting_sources(db)
    teams = db.query(Team).order_by(Team.name).all()
    competitions = db.query(Competition).filter_by(is_active=True).order_by(Competition.name).all()
    return TEMPLATES.TemplateResponse(
        request,
        "admin_fontes.html",
        {
            "sources": sources,
            "teams": teams,
            "competitions": competitions,
            "app_timezone": settings.app_timezone,
        },
    )


@router.post("/admin/fontes/add")
async def admin_fontes_add(request: Request, db: Session = Depends(get_db), user=Depends(admin_required)):
    from palpitaria.services.scouting_preferences import add_scouting_source

    form = await request.form()
    label = str(form.get("label") or "").strip()
    url = str(form.get("url") or "").strip()
    notes = str(form.get("notes") or "").strip() or None
    team_raw = form.get("team_id")
    team_id = int(team_raw) if team_raw else None
    comp_code = str(form.get("competition_code") or "").strip().upper() or None
    try:
        add_scouting_source(
            db,
            label=label,
            url=url,
            team_id=team_id,
            competition_code=comp_code,
            notes=notes,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return RedirectResponse(url="/admin/fontes", status_code=303)


@router.post("/admin/fontes/toggle/{source_id}")
def admin_fontes_toggle(
    source_id: int,
    db: Session = Depends(get_db),
    user=Depends(admin_required),
):
    from palpitaria.services.scouting_preferences import toggle_scouting_source

    toggle_scouting_source(db, source_id)
    return RedirectResponse(url="/admin/fontes", status_code=303)


@router.post("/admin/fontes/delete/{source_id}")
def admin_fontes_delete(
    source_id: int,
    db: Session = Depends(get_db),
    user=Depends(admin_required),
):
    from palpitaria.services.scouting_preferences import delete_scouting_source

    delete_scouting_source(db, source_id)
    return RedirectResponse(url="/admin/fontes", status_code=303)

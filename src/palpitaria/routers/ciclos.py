"""Ciclos de gestão de banca (metas progressivas)."""

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from palpitaria.config import settings
from palpitaria.database import get_db
from palpitaria.deps import TEMPLATES, login_required
from palpitaria.models import Cycle, CycleStep
from palpitaria.services.analyzer import analyze_upcoming
from palpitaria.services.ledger import current_period, period_label

router = APIRouter()


@router.get("/ciclos", response_class=HTMLResponse)
def list_ciclos(request: Request, db: Session = Depends(get_db), user=Depends(login_required)):
    from palpitaria.services.cycle_service import calculate_next_step_target, get_active_cycle

    active_cycle = get_active_cycle(db, user.id)
    past_cycles = db.query(Cycle).filter(Cycle.user_id == user.id, Cycle.status != "ACTIVE").order_by(Cycle.created_at.desc()).limit(10).all()

    next_target_pct = calculate_next_step_target(active_cycle) if active_cycle else 5.0

    # Sugestões de jogos para o ciclo (próximos 3 dias)
    upcoming = analyze_upcoming(db, limit=10, days=3)
    suggestions = [a for a in upcoming if not a.excluded and a.goal_potential_score >= 0.8]

    cy, cm = current_period()
    return TEMPLATES.TemplateResponse(
        request,
        "ciclos.html",
        {
            "active_cycle": active_cycle,
            "past_cycles": past_cycles,
            "next_target_pct": next_target_pct,
            "suggestions": suggestions,
            "current_period": period_label(cy, cm),
            "app_timezone": settings.app_timezone,
        }
    )


@router.post("/ciclos/start")
async def start_cycle(request: Request, db: Session = Depends(get_db), user=Depends(login_required)):
    form = await request.form()
    name = form.get("name") or "Novo Ciclo"
    initial_stake = float(form.get("initial_stake") or 100.0)
    target_amount = initial_stake * 2 # Objetivo padrão: dobrar

    # Fechar ciclo ativo se houver (opcional, ou apenas impedir)
    existing = db.query(Cycle).filter(Cycle.user_id == user.id, Cycle.status == "ACTIVE").first()
    if existing:
        return RedirectResponse(url="/ciclos?error=Ja existe um ciclo ativo", status_code=303)

    cycle = Cycle(
        user_id=user.id,
        name=name,
        initial_stake=initial_stake,
        target_amount=target_amount,
        current_amount=initial_stake,
        status="ACTIVE"
    )
    db.add(cycle)
    db.commit()
    return RedirectResponse(url="/ciclos", status_code=303)


@router.post("/ciclos/step/add")
async def add_step(request: Request, db: Session = Depends(get_db), user=Depends(login_required)):
    from palpitaria.services.cycle_service import add_cycle_step, get_active_cycle

    form = await request.form()
    description = form.get("description")
    fixture_id = form.get("fixture_id")

    cycle = get_active_cycle(db, user.id)
    if not cycle:
        return RedirectResponse(url="/ciclos", status_code=303)

    add_cycle_step(db, cycle, description, fixture_id=int(fixture_id) if fixture_id else None)
    return RedirectResponse(url="/ciclos", status_code=303)


@router.post("/ciclos/step/resolve/{step_id}")
async def resolve_cycle_step(step_id: int, request: Request, db: Session = Depends(get_db), user=Depends(login_required)):
    from palpitaria.services.cycle_service import resolve_step

    form = await request.form()
    outcome = form.get("outcome")

    resolve_step(db, step_id, outcome)
    return RedirectResponse(url="/ciclos", status_code=303)


@router.post("/ciclos/step/delete/{step_id}")
def delete_cycle_step(step_id: int, db: Session = Depends(get_db), user=Depends(login_required)):
    step = db.query(CycleStep).join(Cycle).filter(CycleStep.id == step_id, Cycle.user_id == user.id).first()
    if not step:
        raise HTTPException(status_code=404, detail="Passo não encontrado")

    cycle = step.cycle
    # Se o passo já estava resolvido, precisamos estornar o valor da banca do ciclo
    if step.outcome == "WIN":
        cycle.current_amount = round(cycle.current_amount - step.actual_profit_loss, 2)
        if cycle.status == "COMPLETED":
            cycle.status = "ACTIVE"
            cycle.completed_at = None
    elif step.outcome == "LOSS":
        # Se era um LOSS, a banca tinha ido a zero e o ciclo falhado.
        # Ao deletar, tentamos restaurar a stake que foi perdida.
        cycle.current_amount = step.stake
        cycle.status = "ACTIVE"
        cycle.completed_at = None

    db.delete(step)
    db.commit()
    return RedirectResponse(url="/ciclos", status_code=303)


@router.post("/ciclos/delete/{cycle_id}")
def delete_cycle(cycle_id: int, db: Session = Depends(get_db), user=Depends(login_required)):
    cycle = db.query(Cycle).filter(Cycle.id == cycle_id, Cycle.user_id == user.id).first()
    if cycle:
        db.delete(cycle)
        db.commit()
    return RedirectResponse(url="/ciclos", status_code=303)

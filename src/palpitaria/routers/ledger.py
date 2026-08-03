"""Filiais (branches), apostas, histórico consolidado e gráficos."""

import json
from collections import defaultdict

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import func
from sqlalchemy.orm import Session

from palpitaria.config import settings
from palpitaria.database import get_db
from palpitaria.deps import TEMPLATES, hit_rate_pct, login_required
from palpitaria.models import Bet, Branch, BranchMonthlySummary, Competition, User
from palpitaria.services.ledger import (
    bet_competition_expr,
    bet_created_at_for_period,
    bet_in_period,
    bet_local_period,
    branch_period_choices,
    branch_period_summary,
    close_past_months,
    compute_bet_pl,
    current_period,
    normalize_bet_side,
    period_label,
    resolve_view_period,
)

router = APIRouter()


@router.get("/branches", response_class=HTMLResponse)
def list_branches(
    request: Request,
    comp: str | None = None,
    year: int | None = None,
    month: int | None = None,
    db: Session = Depends(get_db),
    user=Depends(login_required),
):
    close_past_months(db)
    cy, cm = current_period()
    view_y, view_m = resolve_view_period(year, month)
    period_str = period_label(view_y, view_m)
    period_choices = branch_period_choices()

    # Buscar competições ativas — default Todos (sem filtro)
    active_comps = db.query(Competition).filter_by(is_active=True).order_by(Competition.code).all()
    comp_code = (comp or "").strip().upper() or None
    if comp_code == "ALL":
        comp_code = None

    def _branches_for_user() -> list:
        return (
            db.query(Branch)
            .filter(Branch.user_id == user.id)
            .order_by(func.lower(Branch.name))
            .all()
        )

    branches = _branches_for_user()

    # If no branches exist for this user, create defaults
    if not branches:
        over05 = Branch(name="Over 0.5 Goals", slug=f"over_0_5_{user.id}", description="Mercado de pelo menos 1 gol", user_id=user.id)
        over15 = Branch(name="Over 1.5 Goals", slug=f"over_1_5_{user.id}", description="Mercado de pelo menos 2 gols", user_id=user.id)
        db.add_all([over05, over15])
        db.commit()
        branches = _branches_for_user()

    # Calculate P&L summary for each branch
    stats = {}
    for b in branches:
        query = db.query(Bet).filter(Bet.branch_id == b.id)
        if comp_code:
            query = query.filter(bet_competition_expr() == comp_code)

        bets = [bet for bet in query.all() if bet_in_period(bet, view_y, view_m)]
        bets.sort(key=lambda bet: (bet.created_at, bet.id), reverse=True)

        summary = branch_period_summary(db, b.id, view_y, view_m, comp_code)

        if bets:
            total_pl = round(sum(bet.profit_loss for bet in bets), 2)
            win_count = sum(1 for bet in bets if bet.outcome == "WIN")
            loss_count = sum(1 for bet in bets if bet.outcome == "LOSS")
            bet_count = len(bets)
            archived_only = False
            closed_at = None
        elif summary:
            total_pl = round(summary.total_pl, 2)
            win_count = summary.win_count
            loss_count = summary.loss_count
            bet_count = summary.bet_count
            archived_only = True
            closed_at = summary.closed_at
        else:
            total_pl = 0.0
            win_count = 0
            loss_count = 0
            bet_count = 0
            archived_only = False
            closed_at = None

        stats[b.id] = {
            "total_pl": total_pl,
            "win_count": win_count,
            "loss_count": loss_count,
            "bet_count": bet_count,
            "hit_rate_pct": hit_rate_pct(win_count, bet_count),
            "bets": bets,
            "archived_only": archived_only,
            "closed_at": closed_at,
        }

    return TEMPLATES.TemplateResponse(
        request,
        "branches.html",
        {
            "branches": branches,
            "stats": stats,
            "current_period": period_str,
            "selected_year": view_y,
            "selected_month": view_m,
            "period_choices": period_choices,
            "is_current_period": (view_y, view_m) == (cy, cm),
            "app_timezone": settings.app_timezone,
            "active_comps": active_comps,
            "current_comp": comp_code or "",
            "user": user,
        }
    )


@router.get("/historico", response_class=HTMLResponse)
def list_historico(
    request: Request,
    year: int | None = None,
    month: int | None = None,
    db: Session = Depends(get_db),
    user=Depends(login_required),
):
    close_past_months(db)
    cy, cm = current_period()

    branches = db.query(Branch).filter(Branch.user_id == user.id).all()
    branch_by_id = {b.id: b for b in branches}

    summaries = (
        db.query(BranchMonthlySummary)
        .join(Branch)
        .filter(Branch.user_id == user.id)
        .order_by(
            BranchMonthlySummary.year.desc(),
            BranchMonthlySummary.month.desc(),
            BranchMonthlySummary.branch_id,
        )
        .all()
    )

    all_bets = (
        db.query(Bet)
        .join(Branch)
        .filter(Branch.user_id == user.id)
        .all()
    )

    rows = []
    live_period_keys: set[tuple[int, int, int]] = set()

    # Entradas individuais no ledger — fonte primária por filial/mês
    by_period: dict[tuple[int, int, int], list[Bet]] = defaultdict(list)
    for bet in all_bets:
        y, m = bet_local_period(bet.created_at)
        by_period[(y, m, bet.branch_id)].append(bet)
        live_period_keys.add((y, m, bet.branch_id))

    for (y, m, branch_id), bets in by_period.items():
        branch = branch_by_id.get(branch_id)
        if not branch:
            continue
        wins = sum(1 for bet in bets if bet.outcome == "WIN")
        losses = sum(1 for bet in bets if bet.outcome == "LOSS")
        pending = sum(1 for bet in bets if bet.outcome == "PENDING")
        total_pl = round(sum(bet.profit_loss for bet in bets), 2)
        if branch.side == "LAY":
            total_stake = round(sum(bet.stake * (bet.odds - 1) for bet in bets), 2)
        else:
            total_stake = round(sum(bet.stake for bet in bets), 2)
        comp_codes = {bet.competition_code or "WC" for bet in bets}
        rows.append({
            "period": period_label(y, m),
            "year": y,
            "month": m,
            "branch_name": branch.name,
            "bet_count": len(bets),
            "win_count": wins,
            "loss_count": losses,
            "pending_count": pending,
            "total_stake": total_stake,
            "total_pl": total_pl,
            "hit_rate_pct": hit_rate_pct(wins, len(bets)),
            "closed_at": None,
            "competition_code": ", ".join(sorted(comp_codes)),
            "is_active": (y, m) == (cy, cm),
            "side": branch.side,
        })

    # Consolidados só quando não há entradas individuais naquele mês/filial
    consolidated = defaultdict(lambda: {
        "bet_count": 0, "win_count": 0, "loss_count": 0, "pending_count": 0,
        "total_stake": 0.0, "total_pl": 0.0, "closed_at": None, "branch_name": "",
        "comp_codes": set(), "side": "BACK",
    })

    for s in summaries:
        if (s.year, s.month, s.branch_id) in live_period_keys:
            continue
        key = (s.year, s.month, s.branch_id)
        d = consolidated[key]
        d["bet_count"] += s.bet_count
        d["win_count"] += s.win_count
        d["loss_count"] += s.loss_count
        d["pending_count"] += s.pending_count
        d["total_stake"] += s.total_stake
        d["total_pl"] += s.total_pl
        d["branch_name"] = s.branch.name if s.branch else f"Filial #{s.branch_id}"
        d["side"] = s.branch.side if s.branch else "BACK"
        d["comp_codes"].add(s.competition_code)
        if not d["closed_at"] or s.closed_at > d["closed_at"]:
            d["closed_at"] = s.closed_at

    for key in sorted(consolidated.keys(), key=lambda x: (x[0], x[1]), reverse=True):
        d = consolidated[key]
        rows.append({
            "period": period_label(key[0], key[1]),
            "year": key[0],
            "month": key[1],
            "branch_name": d["branch_name"],
            "bet_count": d["bet_count"],
            "win_count": d["win_count"],
            "loss_count": d["loss_count"],
            "pending_count": d["pending_count"],
            "total_stake": d["total_stake"],
            "total_pl": d["total_pl"],
            "hit_rate_pct": hit_rate_pct(d["win_count"], d["bet_count"]),
            "closed_at": d["closed_at"],
            "competition_code": ", ".join(sorted(d["comp_codes"])),
            "is_active": False,
            "side": d["side"],
        })

    rows.sort(
        key=lambda r: (
            0 if r.get("is_active") else 1,
            -r.get("year", 0),
            -r.get("month", 0),
            (r.get("branch_name") or "").lower(),
        )
    )

    # Filtro por mês (opcional). Sem params → todos os meses.
    filter_y = year
    filter_m = month
    period_keys = sorted(
        {(r["year"], r["month"]) for r in rows if r.get("year") and r.get("month")},
        reverse=True,
    )
    if (cy, cm) not in period_keys:
        period_keys.insert(0, (cy, cm))
    period_choices = [
        {"year": y, "month": m, "label": period_label(y, m)} for y, m in period_keys
    ]

    filtered_rows = rows
    if filter_y and filter_m:
        filtered_rows = [r for r in rows if r.get("year") == filter_y and r.get("month") == filter_m]

    current_month_pl = sum(r["total_pl"] for r in rows if r.get("is_active"))
    total_history_pl = sum(r["total_pl"] for r in rows)
    filtered_pl = sum(r["total_pl"] for r in filtered_rows)

    return TEMPLATES.TemplateResponse(
        request,
        "historico.html",
        {
            "rows": filtered_rows,
            "current_period": period_label(cy, cm),
            "app_timezone": settings.app_timezone,
            "user": user,
            "current_month_pl": current_month_pl,
            "total_history_pl": total_history_pl,
            "filtered_pl": filtered_pl,
            "period_choices": period_choices,
            "selected_year": filter_y,
            "selected_month": filter_m,
            "filter_active": bool(filter_y and filter_m),
            "selected_period": period_label(filter_y, filter_m) if filter_y and filter_m else None,
        }
    )


@router.get("/graficos", response_class=HTMLResponse)
def list_graficos(
    request: Request,
    comp: str | None = None,
    db: Session = Depends(get_db),
    user=Depends(login_required),
):
    from palpitaria.services.analytics import build_dashboard_payload

    close_past_months(db)
    active_comps = db.query(Competition).filter_by(is_active=True).all()
    comp_code = comp or None

    payload = build_dashboard_payload(db, user.id, comp_code=comp_code)

    return TEMPLATES.TemplateResponse(
        request,
        "graficos.html",
        {
            "chart_json": json.dumps(payload, ensure_ascii=False),
            "meta": payload["meta"],
            "active_comps": active_comps,
            "current_comp": comp_code,
            "user": user,
        },
    )


@router.post("/admin/finance/update")
async def update_finance(
    request: Request,
    deposits: float = Form(...),
    withdrawals: float = Form(...),
    db: Session = Depends(get_db),
    user=Depends(login_required)
):
    db_user = db.query(User).filter(User.id == user.id).first()
    if db_user:
        db_user.total_deposits = deposits
        db_user.total_withdrawals = withdrawals
        db.commit()
    return RedirectResponse(url="/historico", status_code=303)


@router.post("/user/favorite-comp")
async def set_favorite_comp(
    request: Request,
    comp_code: str = Form(...),
    db: Session = Depends(get_db),
    user=Depends(login_required)
):
    db_user = db.query(User).filter(User.id == user.id).first()
    if db_user:
        db_user.favorite_comp_code = comp_code
        db.commit()

    # Redirecionar de volta para onde estava, mantendo o parâmetro comp se necessário
    referer = request.headers.get("Referer", "/")
    return RedirectResponse(url=referer, status_code=303)


def _parse_bet_period_form(form) -> tuple[int, int]:
    raw = form.get("bet_period")
    if raw and "-" in str(raw):
        y_s, m_s = str(raw).split("-", 1)
        try:
            return resolve_view_period(int(y_s), int(m_s))
        except (TypeError, ValueError):
            pass
    try:
        return resolve_view_period(int(form.get("bet_year")), int(form.get("bet_month")))
    except (TypeError, ValueError):
        return current_period()


def _branches_redirect(
    comp_code: str | None,
    year: int | None = None,
    month: int | None = None,
) -> str:
    params: list[str] = []
    if comp_code:
        params.append(f"comp={comp_code}")
    if year is not None and month is not None:
        params.append(f"year={year}")
        params.append(f"month={month}")
    return f"/branches?{'&'.join(params)}" if params else "/branches"


def _user_bet_or_404(db, bet_id: int, user_id: int):
    bet = (
        db.query(Bet)
        .join(Branch)
        .filter(Bet.id == bet_id, Branch.user_id == user_id)
        .one_or_none()
    )
    if bet is None:
        raise HTTPException(status_code=404, detail="Entrada não encontrada ou acesso negado")
    return bet


@router.post("/branches/add-bet")
async def add_bet(request: Request, db: Session = Depends(get_db), user=Depends(login_required)):
    form = await request.form()
    branch_id = int(form.get("branch_id"))
    comp_code = str(form.get("competition_code") or "").strip().upper()
    if not comp_code:
        raise HTTPException(status_code=400, detail="Selecione o campeonato da entrada")

    # Verificar se a filial pertence ao usuário
    branch = db.query(Branch).filter(Branch.id == branch_id, Branch.user_id == user.id).first()
    if not branch:
        raise HTTPException(status_code=403, detail="Acesso negado")

    description = form.get("description")
    odds = float(form.get("odds"))
    stake = float(form.get("stake"))
    outcome = form.get("outcome") # WIN, LOSS, PENDING
    bet_year, bet_month = _parse_bet_period_form(form)

    commission_rate = branch.commission_rate if branch else 6.5
    # O valor vindo do form agora é sempre a STAKE (o que se quer ganhar no LAY ou apostar no BACK)
    actual_stake = stake

    pl = compute_bet_pl(
        actual_stake, odds, outcome or "PENDING", commission_rate, side=branch.side
    )

    bet = Bet(
        branch_id=branch_id,
        description=description,
        odds=odds,
        stake=actual_stake,  # Salvamos a stake real (o que se ganha)
        outcome=outcome,
        profit_loss=pl,
        competition_code=comp_code,
        created_at=bet_created_at_for_period(bet_year, bet_month),
    )
    db.add(bet)
    db.commit()
    base = _branches_redirect(comp_code, bet_year, bet_month)
    sep = "&" if "?" in base else "?"
    return RedirectResponse(url=f"{base}{sep}saved=1&branch={branch_id}", status_code=303)


@router.post("/branches/delete/{branch_id}")
def delete_branch(branch_id: int, db: Session = Depends(get_db), user=Depends(login_required)):
    branch = db.query(Branch).filter(Branch.id == branch_id, Branch.user_id == user.id).first()
    if not branch:
        raise HTTPException(status_code=403, detail="Acesso negado")

    db.query(Bet).filter(Bet.branch_id == branch_id).delete()
    db.query(Branch).filter(Branch.id == branch_id).delete()
    db.commit()
    return RedirectResponse(url="/branches", status_code=303)


@router.post("/branches/add")
async def add_branch(request: Request, db: Session = Depends(get_db), user=Depends(login_required)):
    form = await request.form()
    name = form.get("name")
    description = form.get("description")
    commission_rate = float(form.get("commission_rate", 6.5))
    side = normalize_bet_side(form.get("side"))
    slug = f"{name.lower().replace(' ', '_')}_{user.id}"

    branch = Branch(
        name=name,
        slug=slug,
        description=description,
        commission_rate=commission_rate,
        side=side,
        user_id=user.id,
    )
    db.add(branch)
    db.commit()
    return RedirectResponse(url="/branches", status_code=303)


@router.post("/branches/bet/update/{bet_id}")
async def update_bet_outcome(bet_id: int, request: Request, db: Session = Depends(get_db), user=Depends(login_required)):
    form = await request.form()
    outcome = form.get("outcome")
    if outcome not in ("WIN", "LOSS", "PENDING"):
        raise HTTPException(status_code=400, detail="Status inválido")

    bet = _user_bet_or_404(db, bet_id, user.id)
    branch = bet.branch
    commission_rate = branch.commission_rate if branch else 6.5

    bet.outcome = outcome
    bet.profit_loss = compute_bet_pl(
        bet.stake, bet.odds, outcome, commission_rate, side=branch.side
    )
    db.commit()
    bet_year, bet_month = _parse_bet_period_form(form)
    return RedirectResponse(
        url=_branches_redirect(form.get("competition_code"), bet_year, bet_month),
        status_code=303,
    )


@router.post("/branches/bet/edit/{bet_id}")
async def edit_bet(bet_id: int, request: Request, db: Session = Depends(get_db), user=Depends(login_required)):
    form = await request.form()
    bet = _user_bet_or_404(db, bet_id, user.id)
    branch = bet.branch

    description = (form.get("description") or "").strip()
    if not description:
        raise HTTPException(status_code=400, detail="Informe o jogo")

    try:
        odds = float(form.get("odds"))
        stake = float(form.get("stake"))
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail="Odd e stake devem ser numéricos") from exc

    if odds <= 1.0 or stake <= 0:
        raise HTTPException(status_code=400, detail="Odd e stake inválidos")

    outcome = form.get("outcome") or "PENDING"
    if outcome not in ("WIN", "LOSS", "PENDING"):
        raise HTTPException(status_code=400, detail="Status inválido")

    commission_rate = branch.commission_rate if branch else 6.5
    bet.description = description[:200]
    bet.odds = odds
    bet.stake = stake
    bet.outcome = outcome
    bet.profit_loss = compute_bet_pl(stake, odds, outcome, commission_rate, side=branch.side)
    db.commit()
    bet_year, bet_month = _parse_bet_period_form(form)
    return RedirectResponse(
        url=_branches_redirect(form.get("competition_code"), bet_year, bet_month),
        status_code=303,
    )


@router.post("/branches/bet/delete/{bet_id}")
async def delete_bet(bet_id: int, request: Request, db: Session = Depends(get_db), user=Depends(login_required)):
    form = await request.form()
    _user_bet_or_404(db, bet_id, user.id)
    db.query(Bet).filter(Bet.id == bet_id).delete()
    db.commit()
    bet_year, bet_month = _parse_bet_period_form(form)
    return RedirectResponse(
        url=_branches_redirect(form.get("competition_code"), bet_year, bet_month),
        status_code=303,
    )

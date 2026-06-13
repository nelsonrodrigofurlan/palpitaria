from collections import deque
from pathlib import Path
from zoneinfo import ZoneInfo

from datetime import datetime

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from palpitaria.config import settings
from palpitaria.database import get_db, init_db
from palpitaria.services.analyzer import (
    analyze_upcoming,
    attach_saved_reports,
    count_teams_with_profiles,
    count_today_fixtures,
    count_upcoming_fixtures,
    get_today_context,
    persist_analysis,
)
from palpitaria.services.explainer import explain_analysis
from palpitaria.services.football_data_client import FootballDataClient, FootballDataError
from palpitaria.services.ingest import build_team_profiles, ingest_world_cup

# Global log buffer for "Nerd Vision"
LOG_BUFFER = deque(maxlen=100)


def add_log(msg: str):
    timestamp = datetime.now().strftime("%H:%M:%S")
    LOG_BUFFER.append(f"[{timestamp}] {msg}")


TEMPLATES = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))


def _format_kickoff(utc_naive: datetime, tz_name: str) -> str:
    kickoff = utc_naive.replace(tzinfo=ZoneInfo("UTC")).astimezone(ZoneInfo(tz_name))
    return kickoff.strftime("%d/%m %H:%M")


TEMPLATES.env.filters["kickoff"] = _format_kickoff

app = FastAPI(title="Palpitaria FC", description="Leitura fundamentada para mercados de gols")
app.mount("/static", StaticFiles(directory=str(Path(__file__).parent / "static")), name="static")


@app.on_event("startup")
def on_startup() -> None:
    init_db()


def _render_home(request: Request, db: Session) -> HTMLResponse:
    today = get_today_context()
    analyses = analyze_upcoming(db, limit=50, for_today_only=True)
    attach_saved_reports(db, analyses)
    candidates = [a for a in analyses if not a.excluded]
    discarded = [a for a in analyses if a.excluded]
    profiles_ready, profiles_total = count_teams_with_profiles(db)
    today_count = count_today_fixtures(db)
    upcoming_count = count_upcoming_fixtures(db)

    return TEMPLATES.TemplateResponse(
        request,
        "index.html",
        {
            "candidates": candidates,
            "discarded": discarded,
            "profiles_ready": profiles_ready,
            "profiles_total": profiles_total,
            "today_label": today.label,
            "today_count": today_count,
            "upcoming_count": upcoming_count,
            "app_timezone": today.timezone,
            "has_token": settings.has_football_token,
            "has_llm": settings.has_llm,
            "llm_provider": settings.llm_provider_label,
            "llm_model": settings.openai_chat_model,
        },
    )


@app.get("/", response_class=HTMLResponse)
def home(request: Request, db: Session = Depends(get_db)) -> HTMLResponse:
    return _render_home(request, db)


@app.post("/sync", response_class=HTMLResponse)
def sync_data(request: Request, db: Session = Depends(get_db)) -> HTMLResponse:
    if not settings.has_football_token:
        raise HTTPException(status_code=400, detail="Configure FOOTBALL_DATA_TOKEN no .env")

    try:
        LOG_BUFFER.clear()
        client = FootballDataClient()
        ingest_result = ingest_world_cup(db, client, log_callback=add_log)
    except FootballDataError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except Exception as exc:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Erro na sincronização: {exc}") from exc

    return TEMPLATES.TemplateResponse(
        request,
        "sync_result.html",
        {
            "ingest": ingest_result,
            "profiles": None,
            "message": "Jogos sincronizados. Agora clique em Atualizar Perfis (~5 min).",
            "redirect": True,
        },
    )


@app.post("/sync-profiles", response_class=HTMLResponse)
def sync_profiles(request: Request, db: Session = Depends(get_db)) -> HTMLResponse:
    if not settings.has_football_token:
        raise HTTPException(status_code=400, detail="Configure FOOTBALL_DATA_TOKEN no .env")

    try:
        LOG_BUFFER.clear()
        client = FootballDataClient()
        profiles = build_team_profiles(db, client, log_callback=add_log)
        ready, total = count_teams_with_profiles(db)
    except FootballDataError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except Exception as exc:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Erro nos perfis: {exc}") from exc

    return TEMPLATES.TemplateResponse(
        request,
        "sync_result.html",
        {
            "ingest": None,
            "profiles": profiles,
            "message": f"Perfis processados: {profiles} chamadas API. Seleções prontas: {ready}/{total}.",
            "redirect": True,
        },
    )


@app.post("/analyze")
def run_analysis(request: Request, db: Session = Depends(get_db)):
    if not settings.has_football_token:
        raise HTTPException(status_code=400, detail="Configure FOOTBALL_DATA_TOKEN no .env")

    today = get_today_context()
    analyses = analyze_upcoming(db, limit=50, for_today_only=True)
    explained = 0
    candidates = 0

    if not analyses:
        detail = f"Nenhum jogo programado para hoje ({today.label})."
        if request.headers.get("HX-Request"):
            return HTMLResponse(
                f'<div class="alert">{detail} Volte no dia do jogo.</div>',
                status_code=200,
            )
        raise HTTPException(status_code=400, detail=detail)

    LOG_BUFFER.clear()
    add_log(f"Iniciando análise de {len(analyses)} jogos...")
    for analysis in analyses:
        add_log(f"Analisando {analysis.home_name} x {analysis.away_name}...")
        explanation = explain_analysis(analysis)
        analysis.llm_explanation = explanation
        persist_analysis(db, analysis, explanation)
        explained += 1
        if not analysis.excluded:
            candidates += 1
            add_log("  -> Candidato qualificado!")
        else:
            add_log(f"  -> Descartado: {', '.join(analysis.exclusion_reasons)}")

    if request.headers.get("HX-Request"):
        return HTMLResponse(
            "",
            status_code=200,
            headers={"HX-Redirect": "/"},
        )

    return RedirectResponse(url="/", status_code=303)


@app.get("/logs")
def get_logs():
    content = "\n".join(LOG_BUFFER)
    return HTMLResponse(f"<pre>{content}</pre>")


@app.get("/branches", response_class=HTMLResponse)
def list_branches(request: Request, db: Session = Depends(get_db)):
    from palpitaria.models import Branch, Bet
    from sqlalchemy import func

    branches = db.query(Branch).all()
    
    # If no branches exist, create defaults
    if not branches:
        over05 = Branch(name="Over 0.5 Goals", slug="over_0_5", description="Mercado de pelo menos 1 gol")
        over15 = Branch(name="Over 1.5 Goals", slug="over_1_5", description="Mercado de pelo menos 2 gols")
        db.add_all([over05, over15])
        db.commit()
        branches = db.query(Branch).all()

    # Calculate P&L summary for each branch
    stats = {}
    for b in branches:
        total_pl = db.query(func.sum(Bet.profit_loss)).filter(Bet.branch_id == b.id).scalar() or 0.0
        win_count = db.query(Bet).filter(Bet.branch_id == b.id, Bet.outcome == "WIN").count()
        loss_count = db.query(Bet).filter(Bet.branch_id == b.id, Bet.outcome == "LOSS").count()
        stats[b.id] = {
            "total_pl": round(total_pl, 2),
            "win_count": win_count,
            "loss_count": loss_count,
            "bets": db.query(Bet).filter(Bet.branch_id == b.id).order_by(Bet.created_at.desc()).limit(10).all()
        }

    return TEMPLATES.TemplateResponse(
        request,
        "branches.html",
        {
            "branches": branches,
            "stats": stats,
        }
    )


@app.post("/branches/add-bet")
async def add_bet(request: Request, db: Session = Depends(get_db)):
    from palpitaria.models import Bet
    
    form = await request.form()
    branch_id = int(form.get("branch_id"))
    description = form.get("description")
    odds = float(form.get("odds"))
    stake = float(form.get("stake"))
    outcome = form.get("outcome") # WIN, LOSS, PENDING
    
    pl = 0.0
    if outcome == "WIN":
        pl = stake * (odds - 1)
    elif outcome == "LOSS":
        pl = -stake

    bet = Bet(
        branch_id=branch_id,
        description=description,
        odds=odds,
        stake=stake,
        outcome=outcome,
        profit_loss=pl
    )
    db.add(bet)
    db.commit()
    return RedirectResponse(url="/branches", status_code=303)


@app.post("/branches/delete/{branch_id}")
def delete_branch(branch_id: int, db: Session = Depends(get_db)):
    from palpitaria.models import Branch, Bet
    db.query(Bet).filter(Bet.branch_id == branch_id).delete()
    db.query(Branch).filter(Branch.id == branch_id).delete()
    db.commit()
    return RedirectResponse(url="/branches", status_code=303)


@app.post("/branches/add")
async def add_branch(request: Request, db: Session = Depends(get_db)):
    from palpitaria.models import Branch
    form = await request.form()
    name = form.get("name")
    description = form.get("description")
    slug = name.lower().replace(" ", "_")
    
    branch = Branch(name=name, slug=slug, description=description)
    db.add(branch)
    db.commit()
    return RedirectResponse(url="/branches", status_code=303)


@app.post("/branches/bet/delete/{bet_id}")
def delete_bet(bet_id: int, db: Session = Depends(get_db)):
    from palpitaria.models import Bet
    db.query(Bet).filter(Bet.id == bet_id).delete()
    db.commit()
    return RedirectResponse(url="/branches", status_code=303)


@app.get("/health")
def health() -> dict:
    return {
        "status": "ok",
        "football_data": settings.has_football_token,
        "llm": settings.has_llm,
        "llm_provider": settings.llm_provider_label,
        "llm_model": settings.openai_chat_model,
    }


def run() -> None:
    import uvicorn

    uvicorn.run("palpitaria.main:app", host="127.0.0.1", port=8000, reload=settings.debug)

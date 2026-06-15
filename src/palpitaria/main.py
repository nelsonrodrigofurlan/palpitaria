from collections import deque
from pathlib import Path
from zoneinfo import ZoneInfo

from datetime import datetime
import os

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
    default_match_context,
    get_today_context,
    persist_analysis,
)
from palpitaria.services.explainer import explain_analysis, refine_best_pick
from palpitaria.services.football_data_client import FootballDataClient, FootballDataError
from palpitaria.services.ingest import build_team_profiles, ingest_world_cup, localize_existing_teams
from palpitaria.services.scraper import enrich_fixture_analysis
from palpitaria.services.wc_profile_web import enrich_today_team_profiles
from palpitaria.models import Fixture

# Global log buffer for "Nerd Vision"
LOG_BUFFER = deque(maxlen=100)


def add_log(msg: str):
    timestamp = datetime.now().strftime("%H:%M:%S")
    LOG_BUFFER.append(f"[{timestamp}] {msg}")


def compute_bet_pl(stake: float, odds: float, outcome: str, commission_rate: float) -> float:
    commission = commission_rate / 100.0
    if outcome == "WIN":
        return stake * (odds - 1) * (1 - commission)
    if outcome == "LOSS":
        return -stake
    return 0.0

TEMPLATES = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))


def _format_kickoff(utc_naive: datetime, tz_name: str) -> str:
    kickoff = utc_naive.replace(tzinfo=ZoneInfo("UTC")).astimezone(ZoneInfo(tz_name))
    return kickoff.strftime("%d/%m %H:%M")


TEMPLATES.env.filters["kickoff"] = _format_kickoff

app = FastAPI(title="Palpitaria FC", description="Leitura fundamentada para mercados de gols")
app.mount("/static", StaticFiles(directory=str(Path(__file__).parent / "static")), name="static")


@app.on_event("startup")
def on_startup() -> None:
    if settings.database_config_error:
        print(f"AVISO: {settings.database_config_error}", flush=True)
    try:
        init_db()
    except Exception as exc:
        msg = str(exc).lower()
        if "translate host" in msg or "getaddrinfo" in msg or "ipv6" in msg or "unreachable" in msg:
            print(
                "\nERRO: não foi possível conectar ao Supabase.\n"
                "Causa provável: DATABASE_URL usa db.PROJECT.supabase.co (só IPv6).\n"
                "Solução: no Supabase Dashboard → Database → Connection pooling → Session,\n"
                "copie a URL do pooler (aws-*-REGION.pooler.supabase.com) para DATABASE_URL no .env\n",
                flush=True,
            )
        raise


def _render_home(request: Request, db: Session) -> HTMLResponse:
    localize_existing_teams(db)
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
        renamed = localize_existing_teams(db)
        if renamed:
            add_log(f"Nomes padronizados PT-BR: {renamed} seleções")
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
            "message": "Jogos sincronizados. No dia do jogo, clique em Atualizar Perfis (só seleções de hoje).",
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
        profiles = build_team_profiles(
            db,
            client,
            log_callback=add_log,
            competition_code=settings.world_cup_code,
            today_only=True,
        )
        ready, total = count_teams_with_profiles(db)
        today_ctx = get_today_context()
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
            "message": (
                f"Perfis API: {profiles} seleção(ões) de hoje ({today_ctx.label}). "
                f"Total prontas no banco: {ready}/{total}. "
                f"Estreias sem jogo na API → passo 3 (perfil web)."
            ),
            "redirect": True,
        },
    )


@app.post("/analyze")
def run_analysis(request: Request, db: Session = Depends(get_db)):
    if not settings.has_football_token:
        raise HTTPException(status_code=400, detail="Configure FOOTBALL_DATA_TOKEN no .env")

    if not settings.has_llm:
        raise HTTPException(
            status_code=400,
            detail="Configure OPENAI_API_KEY no .env para coletar bastidores/contexto e gerar a recomendação.",
        )

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
    add_log(f"Iniciando pipeline de {len(analyses)} jogos (web perfis → API → scrap → recomendação)...")

    add_log("[0/3] Perfis híbridos — API Copa + histórico web das seleções de hoje...")
    web_profiles = enrich_today_team_profiles(db, log_callback=add_log, force_refresh=True)
    add_log(f"  -> {web_profiles} perfil(is) enriquecido(s) via web")

    analyses = analyze_upcoming(db, limit=50, for_today_only=True)
    add_log(f"Reavaliando {len(analyses)} jogos após perfis web...")

    for analysis in analyses:
        fixture = db.query(Fixture).filter_by(id=analysis.fixture_id).one()
        add_log(f"[1/3] Números — {analysis.home_name} x {analysis.away_name} (score {analysis.goal_potential_score})")

        add_log("  [2/3] Scraping bastidores + contexto de jogo...")
        home_insights, away_insights, match_context = enrich_fixture_analysis(
            db,
            fixture_id=analysis.fixture_id,
            external_id=fixture.external_id,
            home_team_id=fixture.home_team_id,
            away_team_id=fixture.away_team_id,
            home_name=analysis.home_name,
            away_name=analysis.away_name,
            excluded=analysis.excluded,
            home_insights=analysis.home_insights,
            away_insights=analysis.away_insights,
            log_callback=add_log,
        )
        analysis.home_insights = home_insights
        analysis.away_insights = away_insights
        analysis.match_context = match_context or default_match_context()

        add_log("  [3/3] Decisão de mercado (LLM + web + bastidores)...")
        analysis.best_pick = refine_best_pick(analysis)
        explanation = explain_analysis(analysis)
        analysis.llm_explanation = explanation
        persist_analysis(db, analysis, explanation)
        explained += 1
        if not analysis.excluded:
            candidates += 1
            add_log("  -> Candidato qualificado!")
        else:
            pick_hint = ""
            if analysis.best_pick:
                pick_hint = f" | Palpite alt.: {analysis.best_pick.get('market', '—')}"
            add_log(f"  -> Descartado (Over): {', '.join(analysis.exclusion_reasons)}{pick_hint}")

    add_log(f"Concluído: {explained} leituras, {candidates} candidatos.")

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
    from palpitaria.models import Bet, Branch
    
    form = await request.form()
    branch_id = int(form.get("branch_id"))
    description = form.get("description")
    odds = float(form.get("odds"))
    stake = float(form.get("stake"))
    outcome = form.get("outcome") # WIN, LOSS, PENDING
    
    branch = db.query(Branch).filter_by(id=branch_id).first()
    commission_rate = branch.commission_rate if branch else 6.5
    pl = compute_bet_pl(stake, odds, outcome or "PENDING", commission_rate)

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
    commission_rate = float(form.get("commission_rate", 6.5))
    slug = name.lower().replace(" ", "_")
    
    branch = Branch(name=name, slug=slug, description=description, commission_rate=commission_rate)
    db.add(branch)
    db.commit()
    return RedirectResponse(url="/branches", status_code=303)


@app.post("/branches/bet/update/{bet_id}")
async def update_bet_outcome(bet_id: int, request: Request, db: Session = Depends(get_db)):
    from palpitaria.models import Bet, Branch

    form = await request.form()
    outcome = form.get("outcome")
    if outcome not in ("WIN", "LOSS", "PENDING"):
        raise HTTPException(status_code=400, detail="Status inválido")

    bet = db.query(Bet).filter(Bet.id == bet_id).one_or_none()
    if bet is None:
        raise HTTPException(status_code=404, detail="Entrada não encontrada")

    branch = db.query(Branch).filter_by(id=bet.branch_id).first()
    commission_rate = branch.commission_rate if branch else 6.5

    bet.outcome = outcome
    bet.profit_loss = compute_bet_pl(bet.stake, bet.odds, outcome, commission_rate)
    db.commit()
    return RedirectResponse(url="/branches", status_code=303)


@app.post("/branches/bet/delete/{bet_id}")
def delete_bet(bet_id: int, db: Session = Depends(get_db)):
    from palpitaria.models import Bet
    db.query(Bet).filter(Bet.id == bet_id).delete()
    db.commit()
    return RedirectResponse(url="/branches", status_code=303)


@app.get("/health")
def health(db: Session = Depends(get_db)) -> dict:
    from palpitaria.models import Fixture, Team

    payload: dict = {
        "status": "ok",
        "config_source": settings.config_source,
        "football_data": settings.has_football_token,
        "llm": settings.has_llm,
        "llm_provider": settings.llm_provider_label,
        "llm_model": settings.openai_chat_model,
        "database": "postgresql",
        "database_host": settings.db_host_label,
        "timezone": settings.app_timezone,
    }
    if settings.database_config_error:
        payload["status"] = "misconfigured"
        payload["database_config_error"] = settings.database_config_error
        from palpitaria.config import _database_env_diagnostics

        payload["database_env"] = _database_env_diagnostics()
        payload["revision"] = os.getenv("K_REVISION")
    try:
        payload["teams"] = db.query(Team).count()
        payload["fixtures"] = db.query(Fixture).count()
        payload["fixtures_today"] = count_today_fixtures(db)
        payload["fixtures_upcoming"] = count_upcoming_fixtures(db)
    except Exception as exc:
        payload["status"] = "degraded"
        payload["database_error"] = str(exc)
    return payload


def run() -> None:
    import uvicorn

    uvicorn.run("palpitaria.main:app", host="127.0.0.1", port=8000, reload=settings.debug)

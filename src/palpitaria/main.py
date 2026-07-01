import json
import threading
from collections import deque
from pathlib import Path
from zoneinfo import ZoneInfo

from datetime import datetime
import os

from fastapi import Depends, FastAPI, HTTPException, Request, Form
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware
from sqlalchemy.orm import Session

from palpitaria.config import settings
from palpitaria.database import get_db, init_db
from palpitaria.services.auth import verify_password, get_user_by_email
from palpitaria.services.analyzer import (
    analyze_upcoming,
    attach_saved_reports,
    count_teams_with_profiles,
    count_today_fixtures,
    count_upcoming_fixtures,
    get_today_context,
    persist_analysis,
)
from palpitaria.services.match_context_utils import default_match_context
from palpitaria.services.explainer import explain_analysis, refine_best_pick
from palpitaria.services.football_data_client import FootballDataClient, FootballDataError
from palpitaria.services.ingest import build_team_profiles, ingest_competition, localize_existing_teams
from palpitaria.services.scraper import enrich_fixture_analysis
from palpitaria.services.wc_profile_web import enrich_today_team_profiles
from palpitaria.services.chat_service import process_user_message
from palpitaria.services.ai_tracker import (
    backfill_from_fixture_reports,
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
from palpitaria.services.ledger import (
    bet_competition_expr,
    bet_created_at_for_period,
    bet_in_period,
    bet_local_period,
    branch_period_choices,
    close_past_months,
    compute_bet_pl,
    current_period,
    migrate_branch_sides,
    normalize_bet_side,
    period_label,
    resolve_view_period,
)
from palpitaria.models import Fixture

# Global log buffer for "Nerd Vision"
LOG_BUFFER = deque(maxlen=100)
PIPELINE_STATE = {
    "active": False,
    "running": False,
    "done": False,
    "error": None,
    "comp": None,
    "cancelled": False,
}
PIPELINE_CANCEL = threading.Event()
_ACTIVE_DB_RUN_ID: int | None = None


def reset_pipeline_state(cancelled: bool = False) -> None:
    PIPELINE_CANCEL.set()
    PIPELINE_STATE.update(
        active=False, running=False, done=True, error=None, comp=None, cancelled=cancelled
    )
    if cancelled:
        add_log("⛔ Pipeline abortado pelo usuário.")
    PIPELINE_CANCEL.clear()


def add_log(msg: str) -> None:
    timestamp = datetime.now().strftime("%H:%M:%S")
    line = f"[{timestamp}] {msg}"
    LOG_BUFFER.append(line)
    run_id = _ACTIVE_DB_RUN_ID
    if run_id is None:
        return
    try:
        from palpitaria.database import SessionLocal
        from palpitaria.services.pipeline_trigger import persist_log_line

        db = SessionLocal()
        try:
            persist_log_line(db, run_id, line)
        finally:
            db.close()
    except Exception:
        pass


def hit_rate_pct(wins: int, total: int) -> int | None:
    """% de acerto no mês: greens ÷ total de entradas."""
    if total <= 0:
        return None
    return round(wins / total * 100)

TEMPLATES = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))


def _format_kickoff(utc_naive: datetime, tz_name: str) -> str:
    kickoff = utc_naive.replace(tzinfo=ZoneInfo("UTC")).astimezone(ZoneInfo(tz_name))
    return kickoff.strftime("%d/%m %H:%M")


TEMPLATES.env.filters["kickoff"] = _format_kickoff
TEMPLATES.env.filters["tojson"] = lambda obj: json.dumps(obj, ensure_ascii=False)

app = FastAPI(title="Palpitaria FC", description="Leitura fundamentada para mercados de gols")
app.add_middleware(SessionMiddleware, secret_key=settings.secret_key)
app.mount("/static", StaticFiles(directory=str(Path(__file__).parent / "static")), name="static")


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
    if user.email != "nelson.r.furlan@gmail.com":
        raise HTTPException(status_code=403, detail="Acesso negado: peça beça pro Pai")
    return user


@app.on_event("startup")
def on_startup() -> None:
    LOG_BUFFER.clear()
    PIPELINE_STATE.update(
        active=False, running=False, done=False, error=None, comp=None, cancelled=False
    )
    PIPELINE_CANCEL.clear()

    if settings.database_config_error:
        print(f"AVISO: {settings.database_config_error}", flush=True)
        return
    try:
        init_db()
        from palpitaria.database import SessionLocal

        db = SessionLocal()
        try:
            migrate_branch_sides(db)
            closed = close_past_months(db)
            if closed:
                print(f"Ledger: {len(closed)} fechamento(s) mensal(is) consolidado(s).", flush=True)
            backfilled = backfill_from_fixture_reports(db)
            if backfilled:
                print(f"IA tracker: {backfilled} recomendação(ões) importadas.", flush=True)
            resolved = resolve_pending_recommendations(db)
            if resolved:
                print(f"IA tracker: {resolved} recomendação(ões) resolvidas.", flush=True)
        finally:
            db.close()
    except Exception as exc:
        msg = str(exc).lower()
        if "translate host" in msg or "getaddrinfo" in msg or "ipv6" in msg or "unreachable" in msg:
            print(
                "\nAVISO: não foi possível conectar ao Supabase no startup.\n"
                "Causa provável: DATABASE_URL usa db.PROJECT.supabase.co (só IPv6).\n"
                "Solução: no Supabase Dashboard → Database → Connection pooling → Session,\n"
                "copie a URL do pooler (aws-*-REGION.pooler.supabase.com) para DATABASE_URL.\n",
                flush=True,
            )
        print(f"AVISO: startup do banco falhou ({exc!r}) — app sobe em modo degradado.", flush=True)


import json
from palpitaria.models import Competition, Fixture
from palpitaria.services.odds_service import fetch_odds_api_data, extract_betfair_odds

def update_competition_odds(db: Session, comp_code: str):
    """Busca odds da Betfair e salva no cache da competição."""
    sport_map = {
        "BSA": "soccer_brazil_campeonato",
        "WC": "soccer_fifa_world_cup",
        "PL": "soccer_epl",
        "PD": "soccer_spain_la_liga",
        "BL1": "soccer_germany_bundesliga",
        "SA": "soccer_italy_serie_a",
        "FL1": "soccer_france_ligue_one",
        "CL": "soccer_uefa_champions_league",
        "EL": "soccer_uefa_europa_league",
    }
    sport_key = sport_map.get(comp_code)
    if not sport_key:
        return

    raw_odds = fetch_odds_api_data(sport=sport_key)
    if isinstance(raw_odds, list):
        odds_list = extract_betfair_odds(raw_odds)
        comp = db.query(Competition).filter_by(code=comp_code).first()
        if comp:
            comp.odds_json = json.dumps(odds_list)
            db.commit()

def _render_home(request: Request, db: Session, user, comp_code: str | None = None) -> HTMLResponse:
    from palpitaria.models import FixtureReport, Competition
    localize_existing_teams(db)
    
    # Buscar competições ativas
    active_comps = db.query(Competition).filter_by(is_active=True).all()
    
    # Se não houver código, tenta o favorito do usuário, senão pega a primeira ativa ou default WC
    if not comp_code:
        if user and user.favorite_comp_code:
            comp_code = user.favorite_comp_code
        else:
            comp_code = active_comps[0].code if active_comps else "WC"
        
    today = get_today_context()
    analyses = analyze_upcoming(db, limit=50, for_today_only=True, competition_code=comp_code)
    attach_saved_reports(db, analyses)
    candidates = [a for a in analyses if not a.excluded]
    discarded = [a for a in analyses if a.excluded]
    profiles_ready, profiles_total = count_teams_with_profiles(db)
    today_count = count_today_fixtures(db, competition_code=comp_code)
    upcoming_count = count_upcoming_fixtures(db, competition_code=comp_code)

    # Buscar última análise realizada para esta competição
    last_report = db.query(FixtureReport).join(Fixture).filter(Fixture.competition_code == comp_code).order_by(FixtureReport.analyzed_at.desc()).first()
    last_analysis_at = last_report.analyzed_at if last_report else None

    # Odds (Lê do cache salvo na competição)
    odds_list = []
    comp = db.query(Competition).filter_by(code=comp_code).first()
    if comp and comp.odds_json:
        try:
            odds_list = json.loads(comp.odds_json)
        except:
            odds_list = []

    from palpitaria.services.pipeline_trigger import pipeline_used_today

    pipeline_used, pipeline_today_run = pipeline_used_today(db, comp_code)
    pipeline_running_here = PIPELINE_STATE["running"] and PIPELINE_STATE.get("comp") == comp_code

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
            "last_analysis_at": last_analysis_at,
            "active_comps": active_comps,
            "current_comp": comp_code,
            "betfair_odds": odds_list,
            "user": user,
            "pipeline_used_today": pipeline_used,
            "pipeline_running": pipeline_running_here,
            "pipeline_today_run": pipeline_today_run,
        },
    )


@app.get("/login", response_class=HTMLResponse)
def login_page(request: Request):
    if request.session.get("user_id"):
        return RedirectResponse(url="/", status_code=303)
    return TEMPLATES.TemplateResponse(request, "login.html", {"error": None})


@app.post("/login", response_class=HTMLResponse)
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
    request.session["terms_accepted"] = True
    return RedirectResponse(url="/", status_code=303)


@app.get("/logout")
def logout(request: Request):
    request.session.clear()
    return RedirectResponse(url="/login", status_code=303)


@app.get("/", response_class=HTMLResponse)
def home(request: Request, comp: str | None = None, db: Session = Depends(get_db), user=Depends(login_required)) -> HTMLResponse:
    return _render_home(request, db, user, comp_code=comp)


@app.post("/sync", response_class=HTMLResponse)
def sync_data(request: Request, comp: str | None = None, db: Session = Depends(get_db), user=Depends(admin_required)) -> HTMLResponse:
    from palpitaria.services.config_service import get_api_config
    token = get_api_config(db, "FOOTBALL_DATA_TOKEN")
    if not token:
        raise HTTPException(status_code=400, detail="Configure FOOTBALL_DATA_TOKEN no Admin ou .env")

    comp_code = comp or settings.world_cup_code
    try:
        LOG_BUFFER.clear()
        add_log(f"[PASSO 1] Sincronizando jogos de {comp_code}...")
        client = FootballDataClient(token=token)
        ingest_result = ingest_competition(db, client, competition_code=comp_code, log_callback=add_log)
        renamed = localize_existing_teams(db)
        if renamed:
            add_log(f"Nomes padronizados PT-BR: {renamed} seleções")
        add_log(f"Concluído: {ingest_result.get('fixtures', 0)} jogos, {ingest_result.get('teams', 0)} seleções.")
        resolved = resolve_pending_recommendations(db, comp_code)
        if resolved:
            add_log(f"IA: {resolved} recomendação(ões) conferidas com placar final.")
    except FootballDataError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except Exception as exc:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Erro na sincronização: {exc}") from exc

    if request.headers.get("HX-Request"):
        return HTMLResponse(
            "",
            status_code=200,
            headers={"HX-Redirect": f"/?comp={comp_code}"},
        )

    return TEMPLATES.TemplateResponse(
        request,
        "sync_result.html",
        {
            "ingest": ingest_result,
            "profiles": None,
            "message": f"Jogos de {comp_code} sincronizados. No dia do jogo, clique em Atualizar Perfis.",
            "redirect": True,
        },
    )


@app.post("/sync-profiles", response_class=HTMLResponse)
def sync_profiles(request: Request, comp: str | None = None, db: Session = Depends(get_db), user=Depends(admin_required)) -> HTMLResponse:
    from palpitaria.services.config_service import get_api_config
    token = get_api_config(db, "FOOTBALL_DATA_TOKEN")
    if not token:
        raise HTTPException(status_code=400, detail="Configure FOOTBALL_DATA_TOKEN no Admin ou .env")

    comp_code = comp or settings.world_cup_code
    try:
        LOG_BUFFER.clear()
        add_log(f"[PASSO 2] Atualizando perfis API — seleções de hoje ({comp_code})...")
        client = FootballDataClient(token=token)
        profiles = build_team_profiles(
            db,
            client,
            log_callback=add_log,
            competition_code=comp_code,
            today_only=True,
        )
        ready, total = count_teams_with_profiles(db)
        today_ctx = get_today_context()
        add_log(f"Concluído: {profiles} perfil(is) hoje. Total no banco: {ready}/{total}.")
    except FootballDataError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except Exception as exc:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Erro nos perfis: {exc}") from exc

    if request.headers.get("HX-Request"):
        return HTMLResponse(
            "",
            status_code=200,
            headers={"HX-Redirect": f"/?comp={comp_code}"},
        )

    return TEMPLATES.TemplateResponse(
        request,
        "sync_result.html",
        {
            "ingest": None,
            "profiles": profiles,
            "message": (
                f"Perfis API: {profiles} seleção(ões) de {comp_code} hoje ({today_ctx.label}). "
                f"Total prontas no banco: {ready}/{total}. "
                f"Estreias sem jogo na API → passo 3 (perfil web)."
            ),
            "redirect": True,
        },
    )


@app.post("/analyze")
def run_analysis(request: Request, comp: str | None = None, db: Session = Depends(get_db), user=Depends(admin_required)):
    LOG_BUFFER.clear()
    comp_code = comp or settings.world_cup_code
    _execute_analysis_pipeline(db, comp_code)
    
    if request.headers.get("HX-Request"):
        return HTMLResponse(
            "",
            status_code=200,
            headers={"HX-Redirect": f"/?comp={comp_code}"},
        )
    return RedirectResponse(url="/", status_code=303)


def _execute_analysis_pipeline(db: Session, comp_code: str):
    from palpitaria.services.config_service import get_api_config
    
    token = get_api_config(db, "FOOTBALL_DATA_TOKEN")
    llm_key = get_api_config(db, "OPENAI_API_KEY")
    llm_base = get_api_config(db, "OPENAI_BASE_URL")

    if not token:
        raise HTTPException(status_code=400, detail="Configure FOOTBALL_DATA_TOKEN no Admin ou .env")

    if not llm_key:
        raise HTTPException(
            status_code=400,
            detail="Configure OPENAI_API_KEY no Admin ou .env para coletar bastidores/contexto e gerar a recomendação.",
        )

    today = get_today_context()
    analyses = analyze_upcoming(db, limit=50, for_today_only=True, competition_code=comp_code)
    explained = 0
    candidates = 0

    if not analyses:
        add_log(f"AVISO: Nenhum jogo de {comp_code} programado para hoje ({today.label}).")
        return

    add_log(f"Iniciando pipeline de {len(analyses)} jogos de {comp_code} (web perfis → API → scrap → recomendação)...")

    add_log(f"[0/3] Perfis híbridos — refresh condicional (API + web, cache {settings.wc_web_profile_refresh_hours}h)...")
    web_profiles = enrich_today_team_profiles(
        db, log_callback=add_log, force_refresh=False, competition_code=comp_code
    )
    add_log(f"  -> {web_profiles} perfil(is) atualizado(s) via web")

    analyses = analyze_upcoming(db, limit=50, for_today_only=True, competition_code=comp_code)
    add_log(f"Reavaliando {len(analyses)} jogos após perfis...")

    from palpitaria.models import FixtureReport
    from palpitaria.services.chat_service import _odds_for_match
    from palpitaria.services.strategy_card import build_strategy_card

    for analysis in analyses:
        fixture = db.query(Fixture).filter_by(id=analysis.fixture_id).one()
        add_log(f"[1/3] Números — {analysis.home_name} x {analysis.away_name} (score {analysis.goal_potential_score})")

        saved_report = db.query(FixtureReport).filter_by(fixture_id=analysis.fixture_id).one_or_none()
        cached_ctx = None
        if saved_report and saved_report.match_context_json:
            try:
                cached_ctx = json.loads(saved_report.match_context_json)
            except json.JSONDecodeError:
                cached_ctx = None

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
            competition_code=comp_code,
            cached_match_context=cached_ctx,
        )
        analysis.home_insights = home_insights
        analysis.away_insights = away_insights
        analysis.match_context = match_context or default_match_context()

        add_log("  [3/3] Decisão + cartão de estratégias (LLM)...")
        settings.openai_api_key = llm_key
        settings.openai_base_url = llm_base

        analysis.best_pick = refine_best_pick(analysis)
        explanation = explain_analysis(analysis)
        analysis.llm_explanation = explanation
        analysis.strategy_card = build_strategy_card(
            analysis,
            odds=_odds_for_match(db, analysis.home_name, analysis.away_name, comp_code),
        )
        persist_analysis(db, analysis, explanation, competition_code=comp_code)
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


@app.post("/pipeline", response_class=HTMLResponse)
def run_full_pipeline(request: Request, comp: str | None = None, db: Session = Depends(get_db), user=Depends(admin_required)):
    from palpitaria.services.config_service import get_api_config
    from palpitaria.services.pipeline_trigger import claim_daily_pipeline_run, finalize_pipeline_run

    comp_code = comp or settings.world_cup_code
    if PIPELINE_STATE["running"]:
        msg = "Já há uma atualização em andamento. Aguarde terminar."
        if request.headers.get("HX-Request"):
            return HTMLResponse(f'<div class="alert">{msg}</div>', status_code=409)
        raise HTTPException(status_code=409, detail=msg)

    try:
        run, _ = claim_daily_pipeline_run(db, comp_code, trigger="web_admin")
    except HTTPException as exc:
        if request.headers.get("HX-Request"):
            return HTMLResponse(f'<div class="alert">🔒 {exc.detail}</div>', status_code=exc.status_code)
        raise

    football_token = get_api_config(db, "FOOTBALL_DATA_TOKEN")
    if not football_token:
        finalize_pipeline_run(db, run.id, error="FOOTBALL_DATA_TOKEN não configurado")
        raise HTTPException(status_code=400, detail="Configure FOOTBALL_DATA_TOKEN no Admin ou .env")

    try:
        _start_pipeline(comp_code, db, football_token, run_id=run.id)
    except HTTPException:
        finalize_pipeline_run(db, run.id, error="Falha ao iniciar pipeline")
        raise
    except Exception as exc:
        finalize_pipeline_run(db, run.id, error=str(exc))
        raise HTTPException(status_code=500, detail="Falha ao iniciar pipeline") from exc

    if request.headers.get("HX-Request"):
        return HTMLResponse("", status_code=202)
    return RedirectResponse(url="/", status_code=303)


def _start_pipeline(comp_code: str, db: Session, football_token: str | None, *, run_id: int | None = None) -> str:
    if PIPELINE_STATE["running"]:
        raise HTTPException(status_code=409, detail="Já há uma atualização em andamento. Aguarde terminar.")
    if not football_token:
        raise HTTPException(status_code=400, detail="Configure FOOTBALL_DATA_TOKEN no Admin ou .env")

    PIPELINE_CANCEL.clear()
    PIPELINE_STATE.update(
        active=True, running=True, done=False, error=None, comp=comp_code, cancelled=False
    )
    LOG_BUFFER.clear()
    add_log(f"🚀 Preparando pipeline completo ({comp_code})...")
    thread = threading.Thread(target=_run_full_pipeline_work, args=(comp_code, run_id), daemon=True)
    thread.start()
    return comp_code


def _pipeline_aborted() -> bool:
    return PIPELINE_CANCEL.is_set()


def _run_full_pipeline_work(comp_code: str, run_id: int | None = None) -> None:
    global _ACTIVE_DB_RUN_ID
    from palpitaria.database import SessionLocal
    from palpitaria.services.config_service import get_api_config
    from palpitaria.services.pipeline_trigger import finalize_pipeline_run

    _ACTIVE_DB_RUN_ID = run_id
    db = SessionLocal()
    pipeline_error: str | None = None
    add_log(f"🚀 INICIANDO PIPELINE COMPLETO ({comp_code})")

    try:
        if _pipeline_aborted():
            raise RuntimeError("Pipeline abortado")

        token = get_api_config(db, "FOOTBALL_DATA_TOKEN")
        if not token:
            raise RuntimeError("Configure FOOTBALL_DATA_TOKEN no Admin ou .env")

        add_log("\n[PASSO 1/3] Sincronizando jogos e resultados...")
        client = FootballDataClient(token=token)
        ingest_result = ingest_competition(db, client, competition_code=comp_code, log_callback=add_log)
        if _pipeline_aborted():
            raise RuntimeError("Pipeline abortado")
        localize_existing_teams(db)
        resolve_pending_recommendations(db, comp_code)
        add_log(f"✓ Jogos sincronizados: {ingest_result.get('fixtures', 0)} novos/atualizados.")

        if _pipeline_aborted():
            raise RuntimeError("Pipeline abortado")

        add_log("\n[PASSO 2/3] Atualizando perfis técnicos (API)...")
        profiles = build_team_profiles(
            db,
            client,
            log_callback=add_log,
            competition_code=comp_code,
            today_only=True,
        )
        if _pipeline_aborted():
            raise RuntimeError("Pipeline abortado")
        ready, total = count_teams_with_profiles(db)
        add_log(f"✓ Perfis API atualizados: {profiles} hoje. Total: {ready}/{total}.")

        if _pipeline_aborted():
            raise RuntimeError("Pipeline abortado")

        add_log("\n[PASSO 3/3] Gerando leituras IA (Web + Scrap + LLM)...")
        _execute_analysis_pipeline(db, comp_code)
        if _pipeline_aborted():
            raise RuntimeError("Pipeline abortado")

        add_log("\n[PASSO 4] Atualizando odds da Betfair...")
        update_competition_odds(db, comp_code)

        add_log("\n✓ PIPELINE CONCLUÍDO COM SUCESSO!")
    except Exception as exc:
        db.rollback()
        if str(exc) == "Pipeline abortado":
            add_log("\n⛔ PIPELINE ABORTADO.")
            PIPELINE_STATE["error"] = None
        else:
            pipeline_error = str(exc)
            add_log(f"\n❌ ERRO NO PIPELINE: {exc}")
            PIPELINE_STATE["error"] = pipeline_error
    finally:
        PIPELINE_STATE["running"] = False
        PIPELINE_STATE["done"] = True
        PIPELINE_STATE["active"] = False
        if run_id is not None:
            finalize_pipeline_run(db, run_id, error=pipeline_error)
        db.close()
        _ACTIVE_DB_RUN_ID = None


@app.post("/api/v1/pipeline/trigger")
def api_trigger_pipeline(
    request: Request,
    comp: str | None = None,
    db: Session = Depends(get_db),
) -> dict:
    from palpitaria.services.config_service import get_api_config
    from palpitaria.services.pipeline_trigger import (
        claim_remote_daily_run,
        verify_trigger_request,
        watch_url_for_token,
    )

    verify_trigger_request(request)
    if PIPELINE_STATE["running"]:
        raise HTTPException(status_code=409, detail="Já há uma atualização em andamento. Aguarde terminar.")

    football_token = get_api_config(db, "FOOTBALL_DATA_TOKEN")
    if not football_token:
        raise HTTPException(status_code=400, detail="Configure FOOTBALL_DATA_TOKEN no Admin ou .env")

    comp_code = comp or settings.world_cup_code
    run, watch_token = claim_remote_daily_run(db, comp_code)
    try:
        _start_pipeline(comp_code, db, football_token, run_id=run.id)
    except HTTPException:
        from palpitaria.services.pipeline_trigger import finalize_pipeline_run

        finalize_pipeline_run(db, run.id, error="Falha ao iniciar pipeline")
        raise
    except Exception as exc:
        from palpitaria.services.pipeline_trigger import finalize_pipeline_run

        finalize_pipeline_run(db, run.id, error=str(exc))
        raise HTTPException(status_code=500, detail="Falha ao iniciar pipeline") from exc

    return {
        "status": "started",
        "run_day": run.run_day,
        "comp": comp_code,
        "watch_token": watch_token,
        "watch_url": watch_url_for_token(watch_token),
    }


@app.get("/api/v1/pipeline/status")
def api_pipeline_status(t: str, db: Session = Depends(get_db)) -> dict:
    from palpitaria.services.pipeline_trigger import get_run_by_watch_token, run_status_payload

    run = get_run_by_watch_token(db, t)
    if run is None:
        raise HTTPException(status_code=404, detail="Token inválido ou expirado.")
    return run_status_payload(run)


@app.get("/api/v1/pipeline/logs")
def api_pipeline_logs(t: str, db: Session = Depends(get_db)) -> HTMLResponse:
    from palpitaria.services.pipeline_trigger import fetch_log_lines, get_run_by_watch_token

    run = get_run_by_watch_token(db, t)
    if run is None:
        raise HTTPException(status_code=404, detail="Token inválido ou expirado.")
    content = "\n".join(fetch_log_lines(db, run.id))
    return HTMLResponse(f"<pre>{content}</pre>")


@app.get("/pipeline/watch", response_class=HTMLResponse)
def pipeline_watch_page(request: Request, t: str | None = None):
    return TEMPLATES.TemplateResponse(
        request,
        "pipeline_watch.html",
        {
            "watch_token": t or "",
            "app_timezone": settings.app_timezone,
        },
    )


@app.post("/pipeline/abort")
def abort_pipeline(user=Depends(admin_required)) -> dict:
    was_active = PIPELINE_STATE["active"] or PIPELINE_STATE["running"]
    PIPELINE_CANCEL.set()  # Sinaliza para a thread parar
    LOG_BUFFER.clear()
    reset_pipeline_state(cancelled=was_active)
    return {"aborted": was_active, "status": "idle"}


@app.get("/pipeline/status")
def pipeline_status(user=Depends(admin_required)) -> dict:
    return {
        "active": PIPELINE_STATE["active"],
        "running": PIPELINE_STATE["running"],
        "done": PIPELINE_STATE["done"],
        "error": PIPELINE_STATE["error"],
        "comp": PIPELINE_STATE["comp"],
        "cancelled": PIPELINE_STATE["cancelled"],
    }


@app.get("/logs")
def get_logs(user=Depends(admin_required)):
    content = "\n".join(LOG_BUFFER)
    return HTMLResponse(f"<pre>{content}</pre>")


@app.get("/branches", response_class=HTMLResponse)
def list_branches(
    request: Request,
    comp: str | None = None,
    year: int | None = None,
    month: int | None = None,
    db: Session = Depends(get_db),
    user=Depends(login_required),
):
    from palpitaria.models import Branch, Bet, Competition, BranchMonthlySummary
    from sqlalchemy import func
    from palpitaria.services.ledger import branch_period_summary

    close_past_months(db)
    cy, cm = current_period()
    view_y, view_m = resolve_view_period(year, month)
    period_str = period_label(view_y, view_m)
    period_choices = branch_period_choices()

    # Buscar competições ativas
    active_comps = db.query(Competition).filter_by(is_active=True).all()
    if not comp:
        comp_code = user.favorite_comp_code or (active_comps[0].code if active_comps else "WC")
    else:
        comp_code = comp

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
            "current_comp": comp_code,
            "user": user,
        }
    )


@app.get("/historico", response_class=HTMLResponse)
def list_historico(request: Request, db: Session = Depends(get_db), user=Depends(login_required)):
    from palpitaria.models import BranchMonthlySummary, Branch, Bet
    from collections import defaultdict

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

    for (year, month, branch_id), bets in by_period.items():
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
            "period": period_label(year, month),
            "year": year,
            "month": month,
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
            "is_active": (year, month) == (cy, cm),
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

    current_month_pl = sum(r["total_pl"] for r in rows if r.get("is_active"))
    total_history_pl = sum(r["total_pl"] for r in rows)

    return TEMPLATES.TemplateResponse(
        request,
        "historico.html",
        {
            "rows": rows,
            "current_period": period_label(cy, cm),
            "app_timezone": settings.app_timezone,
            "user": user,
            "current_month_pl": current_month_pl,
            "total_history_pl": total_history_pl,
        }
    )


@app.get("/graficos", response_class=HTMLResponse)
def list_graficos(
    request: Request,
    comp: str | None = None,
    db: Session = Depends(get_db),
    user=Depends(login_required),
):
    from palpitaria.models import Competition
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


@app.post("/admin/finance/update")
async def update_finance(
    request: Request,
    deposits: float = Form(...),
    withdrawals: float = Form(...),
    db: Session = Depends(get_db),
    user=Depends(login_required)
):
    from palpitaria.models import User
    db_user = db.query(User).filter(User.id == user.id).first()
    if db_user:
        db_user.total_deposits = deposits
        db_user.total_withdrawals = withdrawals
        db.commit()
    return RedirectResponse(url="/historico", status_code=303)


@app.post("/user/favorite-comp")
async def set_favorite_comp(
    request: Request,
    comp_code: str = Form(...),
    db: Session = Depends(get_db),
    user=Depends(login_required)
):
    from palpitaria.models import User
    db_user = db.query(User).filter(User.id == user.id).first()
    if db_user:
        db_user.favorite_comp_code = comp_code
        db.commit()
    
    # Redirecionar de volta para onde estava, mantendo o parâmetro comp se necessário
    referer = request.headers.get("Referer", "/")
    return RedirectResponse(url=referer, status_code=303)


@app.get("/ia-historico", response_class=HTMLResponse)
def list_ia_historico(
    request: Request,
    comp: str | None = None,
    mes: str | None = None,
    db: Session = Depends(get_db),
    user=Depends(login_required),
):
    from palpitaria.models import AiRecommendation, Competition

    active_comps = db.query(Competition).filter_by(is_active=True).all()
    comp_code = comp or user.favorite_comp_code or (active_comps[0].code if active_comps else "WC")

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
    all_recommendations = (
        db.query(AiRecommendation)
        .filter(AiRecommendation.competition_code == comp_code)
        .order_by(AiRecommendation.analyzed_at.desc())
        .limit(500)
        .all()
    )
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
            "current_comp": comp_code,
            "user": user,
        },
    )


@app.get("/sobre", response_class=HTMLResponse)
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


@app.get("/leitura/gestao-de-banca", response_class=HTMLResponse)
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


@app.get("/legal/aviso-legal", response_class=HTMLResponse)
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


@app.get("/chat", response_class=HTMLResponse)
def chat_page(request: Request, db: Session = Depends(get_db), user=Depends(login_required)):
    from palpitaria.services.chat_service import fetch_user_chat_history, user_chat_daily_quota

    cy, cm = current_period()
    history = fetch_user_chat_history(db, user.id, ascending=True)
    quota = user_chat_daily_quota(db, user.id, user.email)
    return TEMPLATES.TemplateResponse(
        request,
        "chat.html",
        {
            "current_period": period_label(cy, cm),
            "app_timezone": settings.app_timezone,
            "history": history,
            "quota": quota,
        },
    )


@app.post("/chat/send", response_class=HTMLResponse)
async def chat_send(request: Request, db: Session = Depends(get_db), user=Depends(login_required)):
    from palpitaria.services.chat_service import process_user_message, user_chat_daily_quota
    from palpitaria.services.config_service import get_api_config

    quota = user_chat_daily_quota(db, user.id, user.email)
    if quota["blocked"]:
        return TEMPLATES.TemplateResponse(
            request,
            "partials/chat_limit.html",
            {"quota": quota, "app_timezone": settings.app_timezone},
        )

    llm_key = get_api_config(db, "OPENAI_API_KEY")
    llm_base = get_api_config(db, "OPENAI_BASE_URL")
    if llm_key:
        settings.openai_api_key = llm_key
        settings.openai_base_url = llm_base

    form = await request.form()
    message = form.get("message")
    if not message:
        return ""
    
    result = process_user_message(db, message, user_id=user.id)
    quota_after = user_chat_daily_quota(db, user.id, user.email)

    return TEMPLATES.TemplateResponse(
        request,
        "partials/chat_message.html",
        {
            "user_message": message,
            "ai_response": result.get("response"),
            "is_valid": result.get("is_valid"),
            "evaluation": result.get("evaluation"),
            "verdict": result.get("verdict"),
            "quota": quota_after,
            "app_timezone": settings.app_timezone,
        },
    )


@app.post("/branches/add-bet")
async def add_bet(request: Request, db: Session = Depends(get_db), user=Depends(login_required)):
    from palpitaria.models import Bet, Branch
    
    form = await request.form()
    branch_id = int(form.get("branch_id"))
    comp_code = form.get("competition_code")
    
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
        competition_code=comp_code or settings.world_cup_code,
        created_at=bet_created_at_for_period(bet_year, bet_month),
    )
    db.add(bet)
    db.commit()
    base = _branches_redirect(comp_code, bet_year, bet_month)
    sep = "&" if "?" in base else "?"
    return RedirectResponse(url=f"{base}{sep}saved=1&branch={branch_id}", status_code=303)


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
    from palpitaria.models import Bet, Branch

    bet = (
        db.query(Bet)
        .join(Branch)
        .filter(Bet.id == bet_id, Branch.user_id == user_id)
        .one_or_none()
    )
    if bet is None:
        raise HTTPException(status_code=404, detail="Entrada não encontrada ou acesso negado")
    return bet


@app.post("/branches/delete/{branch_id}")
def delete_branch(branch_id: int, db: Session = Depends(get_db), user=Depends(login_required)):
    from palpitaria.models import Branch, Bet
    branch = db.query(Branch).filter(Branch.id == branch_id, Branch.user_id == user.id).first()
    if not branch:
        raise HTTPException(status_code=403, detail="Acesso negado")
    
    db.query(Bet).filter(Bet.branch_id == branch_id).delete()
    db.query(Branch).filter(Branch.id == branch_id).delete()
    db.commit()
    return RedirectResponse(url="/branches", status_code=303)


@app.post("/branches/add")
async def add_branch(request: Request, db: Session = Depends(get_db), user=Depends(login_required)):
    from palpitaria.models import Branch
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


@app.post("/branches/bet/update/{bet_id}")
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


@app.post("/branches/bet/edit/{bet_id}")
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


@app.post("/branches/bet/delete/{bet_id}")
async def delete_bet(bet_id: int, request: Request, db: Session = Depends(get_db), user=Depends(login_required)):
    from palpitaria.models import Bet

    form = await request.form()
    _user_bet_or_404(db, bet_id, user.id)
    db.query(Bet).filter(Bet.id == bet_id).delete()
    db.commit()
    bet_year, bet_month = _parse_bet_period_form(form)
    return RedirectResponse(
        url=_branches_redirect(form.get("competition_code"), bet_year, bet_month),
        status_code=303,
    )


@app.get("/ciclos", response_class=HTMLResponse)
def list_ciclos(request: Request, db: Session = Depends(get_db), user=Depends(login_required)):
    from palpitaria.models import Cycle, Fixture
    from palpitaria.services.cycle_service import get_active_cycle, calculate_next_step_target
    from palpitaria.services.analyzer import analyze_upcoming

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


@app.post("/ciclos/start")
async def start_cycle(request: Request, db: Session = Depends(get_db), user=Depends(login_required)):
    from palpitaria.models import Cycle
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


@app.post("/ciclos/step/add")
async def add_step(request: Request, db: Session = Depends(get_db), user=Depends(login_required)):
    from palpitaria.services.cycle_service import get_active_cycle, add_cycle_step
    form = await request.form()
    description = form.get("description")
    fixture_id = form.get("fixture_id")
    
    cycle = get_active_cycle(db, user.id)
    if not cycle:
        return RedirectResponse(url="/ciclos", status_code=303)
        
    add_cycle_step(db, cycle, description, fixture_id=int(fixture_id) if fixture_id else None)
    return RedirectResponse(url="/ciclos", status_code=303)


@app.post("/ciclos/step/resolve/{step_id}")
async def resolve_cycle_step(step_id: int, request: Request, db: Session = Depends(get_db), user=Depends(login_required)):
    from palpitaria.services.cycle_service import resolve_step
    form = await request.form()
    outcome = form.get("outcome")
    
    resolve_step(db, step_id, outcome)
    return RedirectResponse(url="/ciclos", status_code=303)


@app.post("/ciclos/step/delete/{step_id}")
def delete_cycle_step(step_id: int, db: Session = Depends(get_db), user=Depends(login_required)):
    from palpitaria.models import CycleStep, Cycle
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


@app.post("/ciclos/delete/{cycle_id}")
def delete_cycle(cycle_id: int, db: Session = Depends(get_db), user=Depends(login_required)):
    from palpitaria.models import Cycle
    cycle = db.query(Cycle).filter(Cycle.id == cycle_id, Cycle.user_id == user.id).first()
    if cycle:
        db.delete(cycle)
        db.commit()
    return RedirectResponse(url="/ciclos", status_code=303)


@app.get("/admin/users", response_class=HTMLResponse)
def admin_users(request: Request, db: Session = Depends(get_db), user=Depends(admin_required)):
    from palpitaria.models import User
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


@app.post("/admin/users/add")
async def admin_add_user(request: Request, db: Session = Depends(get_db), user=Depends(admin_required)):
    from palpitaria.models import User
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


@app.post("/admin/users/toggle/{target_id}")
def admin_toggle_user(target_id: int, db: Session = Depends(get_db), user=Depends(admin_required)):
    from palpitaria.models import User
    target = db.query(User).filter(User.id == target_id).first()
    if target and target.email != "nelson.r.furlan@gmail.com":
        target.is_active = not target.is_active
        db.commit()
    return RedirectResponse(url="/admin/users", status_code=303)


@app.post("/admin/users/delete/{target_id}")
def admin_delete_user(target_id: int, db: Session = Depends(get_db), user=Depends(admin_required)):
    from palpitaria.models import User
    target = db.query(User).filter(User.id == target_id).first()
    if target and target.email != "nelson.r.furlan@gmail.com":
        db.delete(target)
        db.commit()
    return RedirectResponse(url="/admin/users", status_code=303)


@app.get("/admin/config", response_class=HTMLResponse)
def admin_config(request: Request, db: Session = Depends(get_db), user=Depends(admin_required)):
    from palpitaria.models import ApiConfig, Competition
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


@app.post("/admin/config/api/update")
async def admin_update_api_config(request: Request, db: Session = Depends(get_db), user=Depends(admin_required)):
    from palpitaria.models import ApiConfig
    form = await request.form()
    key = form.get("key")
    value = form.get("value")
    
    cfg = db.query(ApiConfig).filter_by(key=key).first()
    if cfg:
        cfg.value = value
        db.commit()
    return RedirectResponse(url="/admin/config", status_code=303)


@app.post("/admin/config/competition/add")
async def admin_add_competition(request: Request, db: Session = Depends(get_db), user=Depends(admin_required)):
    from palpitaria.models import Competition
    form = await request.form()
    code = form.get("code").upper()
    name = form.get("name")
    season = int(form.get("season", 2026))
    
    if not db.query(Competition).filter_by(code=code).first():
        new_comp = Competition(code=code, name=name, season=season, is_active=True)
        db.add(new_comp)
        db.commit()
    return RedirectResponse(url="/admin/config", status_code=303)


@app.post("/admin/config/competition/toggle/{comp_id}")
def admin_toggle_competition(comp_id: int, db: Session = Depends(get_db), user=Depends(admin_required)):
    from palpitaria.models import Competition
    comp = db.query(Competition).filter_by(id=comp_id).first()
    if comp:
        comp.is_active = not comp.is_active
        db.commit()
    return RedirectResponse(url="/admin/config", status_code=303)


@app.get("/admin/custos", response_class=HTMLResponse)
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


@app.get("/admin/skills", response_class=HTMLResponse)
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


@app.get("/admin/fontes", response_class=HTMLResponse)
def admin_fontes(request: Request, db: Session = Depends(get_db), user=Depends(admin_required)):
    from palpitaria.models import Competition, Team
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


@app.post("/admin/fontes/add")
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


@app.post("/admin/fontes/toggle/{source_id}")
def admin_fontes_toggle(
    source_id: int,
    db: Session = Depends(get_db),
    user=Depends(admin_required),
):
    from palpitaria.services.scouting_preferences import toggle_scouting_source

    toggle_scouting_source(db, source_id)
    return RedirectResponse(url="/admin/fontes", status_code=303)


@app.post("/admin/fontes/delete/{source_id}")
def admin_fontes_delete(
    source_id: int,
    db: Session = Depends(get_db),
    user=Depends(admin_required),
):
    from palpitaria.services.scouting_preferences import delete_scouting_source

    delete_scouting_source(db, source_id)
    return RedirectResponse(url="/admin/fontes", status_code=303)


@app.get("/health/live")
def health_live() -> dict:
    """Liveness — Cloud Run só precisa saber que o processo escutou na porta."""
    return {"status": "ok"}


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

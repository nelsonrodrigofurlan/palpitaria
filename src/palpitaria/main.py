import json
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
    filter_recommendations_by_month,
    market_rows_from_stats,
    parse_month_param,
    resolve_pending_recommendations,
    rows_for_scope,
)
from palpitaria.services.ledger import bet_competition_expr, close_past_months, current_period, period_label
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
    if settings.database_config_error:
        print(f"AVISO: {settings.database_config_error}", flush=True)
        return
    try:
        init_db()
        from palpitaria.database import SessionLocal

        db = SessionLocal()
        try:
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


def _render_home(request: Request, db: Session, comp_code: str | None = None) -> HTMLResponse:
    from palpitaria.models import FixtureReport, Competition
    localize_existing_teams(db)
    
    # Buscar competições ativas
    active_comps = db.query(Competition).filter_by(is_active=True).all()
    
    # Se não houver código, pega a primeira ativa ou default WC
    if not comp_code:
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
    return _render_home(request, db, comp_code=comp)


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

    add_log(f"[0/3] Perfis híbridos — API {comp_code} + histórico web das seleções de hoje...")
    web_profiles = enrich_today_team_profiles(db, log_callback=add_log, force_refresh=True, competition_code=comp_code)
    add_log(f"  -> {web_profiles} perfil(is) enriquecido(s) via web")

    analyses = analyze_upcoming(db, limit=50, for_today_only=True, competition_code=comp_code)
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
        settings.openai_api_key = llm_key
        settings.openai_base_url = llm_base
        
        analysis.best_pick = refine_best_pick(analysis)
        explanation = explain_analysis(analysis)
        analysis.llm_explanation = explanation
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
    
    token = get_api_config(db, "FOOTBALL_DATA_TOKEN")
    if not token:
        raise HTTPException(status_code=400, detail="Configure FOOTBALL_DATA_TOKEN no Admin ou .env")

    comp_code = comp or settings.world_cup_code
    
    LOG_BUFFER.clear()
    add_log(f"🚀 INICIANDO PIPELINE COMPLETO ({comp_code})")
    
    try:
        # PASSO 1: Sync Data
        add_log("\n[PASSO 1/3] Sincronizando jogos e resultados...")
        client = FootballDataClient(token=token)
        ingest_result = ingest_competition(db, client, competition_code=comp_code, log_callback=add_log)
        localize_existing_teams(db)
        resolve_pending_recommendations(db, comp_code)
        add_log(f"✓ Jogos sincronizados: {ingest_result.get('fixtures', 0)} novos/atualizados.")

        # PASSO 2: Sync Profiles
        add_log("\n[PASSO 2/3] Atualizando perfis técnicos (API)...")
        profiles = build_team_profiles(
            db,
            client,
            log_callback=add_log,
            competition_code=comp_code,
            today_only=True,
        )
        ready, total = count_teams_with_profiles(db)
        add_log(f"✓ Perfis API atualizados: {profiles} hoje. Total: {ready}/{total}.")

        # PASSO 3: Analyze
        add_log("\n[PASSO 3/3] Gerando leituras IA (Web + Scrap + LLM)...")
        _execute_analysis_pipeline(db, comp_code)
        add_log("\n✓ PIPELINE CONCLUÍDO COM SUCESSO!")

    except Exception as exc:
        db.rollback()
        add_log(f"\n❌ ERRO NO PIPELINE: {exc}")
        raise HTTPException(status_code=500, detail=f"Erro no pipeline: {exc}")

    if request.headers.get("HX-Request"):
        return HTMLResponse(
            "",
            status_code=200,
            headers={"HX-Redirect": f"/?comp={comp_code}"},
        )
    return RedirectResponse(url="/", status_code=303)


@app.get("/logs")
def get_logs(user=Depends(admin_required)):
    content = "\n".join(LOG_BUFFER)
    return HTMLResponse(f"<pre>{content}</pre>")


@app.get("/branches", response_class=HTMLResponse)
def list_branches(request: Request, comp: str | None = None, db: Session = Depends(get_db), user=Depends(login_required)):
    from palpitaria.models import Branch, Bet, Competition
    from sqlalchemy import func, extract

    close_past_months(db)
    cy, cm = current_period()
    period_str = period_label(cy, cm)

    # Buscar competições ativas
    active_comps = db.query(Competition).filter_by(is_active=True).all()
    comp_code = comp or (active_comps[0].code if active_comps else "WC")

    # Filtrar filiais do usuário
    branches = db.query(Branch).filter(Branch.user_id == user.id).all()
    
    # If no branches exist for this user, create defaults
    if not branches:
        over05 = Branch(name="Over 0.5 Goals", slug=f"over_0_5_{user.id}", description="Mercado de pelo menos 1 gol", user_id=user.id)
        over15 = Branch(name="Over 1.5 Goals", slug=f"over_1_5_{user.id}", description="Mercado de pelo menos 2 gols", user_id=user.id)
        db.add_all([over05, over15])
        db.commit()
        branches = db.query(Branch).filter(Branch.user_id == user.id).all()

    # Calculate P&L summary for each branch
    stats = {}
    for b in branches:
        query = db.query(Bet).filter(
            Bet.branch_id == b.id,
            extract("year", Bet.created_at) == cy,
            extract("month", Bet.created_at) == cm
        )
        if comp_code:
            query = query.filter(bet_competition_expr() == comp_code)

        bets = query.order_by(Bet.created_at.desc()).all()
        
        total_pl = sum(bet.profit_loss for bet in bets)
        win_count = sum(1 for bet in bets if bet.outcome == "WIN")
        loss_count = sum(1 for bet in bets if bet.outcome == "LOSS")
        bet_count = len(bets)
        
        stats[b.id] = {
            "total_pl": round(total_pl, 2),
            "win_count": win_count,
            "loss_count": loss_count,
            "bet_count": bet_count,
            "hit_rate_pct": hit_rate_pct(win_count, bet_count),
            "bets": bets[:10]
        }

    return TEMPLATES.TemplateResponse(
        request,
        "branches.html",
        {
            "branches": branches,
            "stats": stats,
            "current_period": period_str,
            "app_timezone": settings.app_timezone,
            "active_comps": active_comps,
            "current_comp": comp_code,
        }
    )


@app.get("/historico", response_class=HTMLResponse)
def list_historico(request: Request, comp: str | None = None, db: Session = Depends(get_db), user=Depends(login_required)):
    from palpitaria.models import BranchMonthlySummary, Branch, Competition

    close_past_months(db)
    cy, cm = current_period()

    # Buscar competições ativas
    active_comps = db.query(Competition).filter_by(is_active=True).all()
    comp_code = comp or (active_comps[0].code if active_comps else "WC")

    query = (
        db.query(BranchMonthlySummary)
        .join(Branch)
        .filter(Branch.user_id == user.id)
    )
    if comp_code:
        query = query.filter(BranchMonthlySummary.competition_code == comp_code)
        
    summaries = query.order_by(
        BranchMonthlySummary.year.desc(),
        BranchMonthlySummary.month.desc(),
        BranchMonthlySummary.branch_id,
    ).all()

    rows = []
    for s in summaries:
        rows.append(
            {
                "period": period_label(s.year, s.month),
                "branch_name": s.branch.name if s.branch else f"Filial #{s.branch_id}",
                "bet_count": s.bet_count,
                "win_count": s.win_count,
                "loss_count": s.loss_count,
                "pending_count": s.pending_count,
                "total_stake": s.total_stake,
                "total_pl": s.total_pl,
                "hit_rate_pct": hit_rate_pct(s.win_count, s.bet_count),
                "closed_at": s.closed_at,
                "competition_code": s.competition_code,
            }
        )

    return TEMPLATES.TemplateResponse(
        request,
        "historico.html",
        {
            "rows": rows,
            "current_period": period_label(cy, cm),
            "app_timezone": settings.app_timezone,
            "active_comps": active_comps,
            "current_comp": comp_code,
        }
    )


@app.get("/ia-historico", response_class=HTMLResponse)
def list_ia_historico(
    request: Request,
    comp: str | None = None,
    mes: str | None = None,
    db: Session = Depends(get_db),
    user=Depends(login_required),
):
    from palpitaria.models import AiRecommendation, Competition

    resolve_pending_recommendations(db, comp)

    active_comps = db.query(Competition).filter_by(is_active=True).all()
    comp_code = comp or (active_comps[0].code if active_comps else "WC")

    query = db.query(AiRecommendation).order_by(AiRecommendation.analyzed_at.desc())
    if comp_code:
        query = query.filter(AiRecommendation.competition_code == comp_code)
    all_recommendations = query.limit(500).all()

    month_options = build_month_options(all_recommendations)
    year, month = parse_month_param(mes)
    selected_mes = f"{year}-{month:02d}"
    selected_period = period_label(year, month)

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
    from palpitaria.models import UserInsight
    cy, cm = current_period()
    # Pegar as últimas interações do usuário
    history = db.query(UserInsight).filter(UserInsight.user_id == user.id).order_by(UserInsight.created_at.desc()).limit(20).all()
    return TEMPLATES.TemplateResponse(
        request,
        "chat.html",
        {
            "current_period": period_label(cy, cm),
            "app_timezone": settings.app_timezone,
            "history": reversed(history),
        }
    )


@app.post("/chat/send", response_class=HTMLResponse)
async def chat_send(request: Request, db: Session = Depends(get_db), user=Depends(login_required)):
    from palpitaria.services.config_service import get_api_config
    
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
    
    return TEMPLATES.TemplateResponse(
        request,
        "partials/chat_message.html",
        {
            "user_message": message,
            "ai_response": result.get("response"),
            "is_valid": result.get("is_valid"),
            "evaluation": result.get("evaluation"),
        }
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
    
    commission_rate = branch.commission_rate if branch else 6.5
    pl = compute_bet_pl(stake, odds, outcome or "PENDING", commission_rate)

    bet = Bet(
        branch_id=branch_id,
        description=description,
        odds=odds,
        stake=stake,
        outcome=outcome,
        profit_loss=pl,
        competition_code=comp_code or settings.world_cup_code
    )
    db.add(bet)
    db.commit()
    return RedirectResponse(url=f"/branches?comp={comp_code}" if comp_code else "/branches", status_code=303)


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
    slug = f"{name.lower().replace(' ', '_')}_{user.id}"
    
    branch = Branch(name=name, slug=slug, description=description, commission_rate=commission_rate, user_id=user.id)
    db.add(branch)
    db.commit()
    return RedirectResponse(url="/branches", status_code=303)


@app.post("/branches/bet/update/{bet_id}")
async def update_bet_outcome(bet_id: int, request: Request, db: Session = Depends(get_db), user=Depends(login_required)):
    from palpitaria.models import Bet, Branch

    form = await request.form()
    outcome = form.get("outcome")
    if outcome not in ("WIN", "LOSS", "PENDING"):
        raise HTTPException(status_code=400, detail="Status inválido")

    bet = db.query(Bet).join(Branch).filter(Bet.id == bet_id, Branch.user_id == user.id).one_or_none()
    if bet is None:
        raise HTTPException(status_code=404, detail="Entrada não encontrada ou acesso negado")

    branch = bet.branch
    commission_rate = branch.commission_rate if branch else 6.5

    bet.outcome = outcome
    bet.profit_loss = compute_bet_pl(bet.stake, bet.odds, outcome, commission_rate)
    db.commit()
    return RedirectResponse(url="/branches", status_code=303)


@app.post("/branches/bet/delete/{bet_id}")
def delete_bet(bet_id: int, db: Session = Depends(get_db), user=Depends(login_required)):
    from palpitaria.models import Bet, Branch
    bet = db.query(Bet).join(Branch).filter(Bet.id == bet_id, Branch.user_id == user.id).one_or_none()
    if not bet:
        raise HTTPException(status_code=403, detail="Acesso negado")
    
    db.query(Bet).filter(Bet.id == bet_id).delete()
    db.commit()
    return RedirectResponse(url="/branches", status_code=303)


@app.get("/ciclos", response_class=HTMLResponse)
def list_ciclos(request: Request, db: Session = Depends(get_db), user=Depends(login_required)):
    from palpitaria.models import Cycle, Fixture
    from palpitaria.services.cycle_service import get_active_cycle, calculate_next_step_target
    from palpitaria.services.analyzer import analyze_upcoming

    active_cycle = get_active_cycle(db, user.id)
    past_cycles = db.query(Cycle).filter(Cycle.user_id == user.id, Cycle.status != "ACTIVE").order_by(Cycle.created_at.desc()).limit(10).all()
    
    next_target_pct = calculate_next_step_target(active_cycle) if active_cycle else 5.0
    
    # Sugestões de jogos para o ciclo (jogos de hoje com 6/6 OK ou alta pontuação)
    upcoming = analyze_upcoming(db, limit=10, for_today_only=True)
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

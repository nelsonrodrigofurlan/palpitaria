"""Home (dashboard) + sync/perfis/análise/pipeline completo — e todo o estado em memória do pipeline."""

import json
import threading
from collections import deque
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from palpitaria.config import settings
from palpitaria.database import get_db
from palpitaria.deps import TEMPLATES, admin_required, login_required
from palpitaria.models import Competition, Fixture, FixtureReport
from palpitaria.services.ai_tracker import resolve_pending_recommendations
from palpitaria.services.analyzer import (
    analyze_upcoming,
    attach_saved_reports,
    count_teams_with_profiles,
    count_today_fixtures,
    count_upcoming_fixtures,
    get_today_context,
    persist_analysis,
)
from palpitaria.services.explainer import refine_best_pick
from palpitaria.services.football_data_client import FootballDataClient, FootballDataError
from palpitaria.services.ingest import build_team_profiles, ingest_competition, localize_existing_teams
from palpitaria.services.match_context_utils import default_match_context
from palpitaria.services.odds_service import update_competition_odds
from palpitaria.services.scraper import enrich_fixture_analysis
from palpitaria.services.wc_profile_web import enrich_today_team_profiles

router = APIRouter()

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


def _comp_scope(comp: str | None) -> str:
    """Escopo do pipeline/sync: código único ou ALL (visão geral)."""
    code = (comp or "").strip().upper()
    return code if code and code != "ALL" else "ALL"


def _home_redirect_for_scope(scope: str) -> str:
    if not scope or scope == "ALL":
        return "/"
    return f"/?comp={scope}"


def _codes_for_scope(db: Session, scope: str) -> list[str]:
    from palpitaria.services.competitions import active_competition_codes, resolve_competition_codes

    if scope == "ALL":
        return active_competition_codes(db)
    return resolve_competition_codes(db, competition_code=scope)


def _render_home(request: Request, db: Session, user, comp_code: str | None = None) -> HTMLResponse:
    from palpitaria.services.competitions import active_competition_codes, resolve_competition_codes

    # localize_existing_teams roda no sync/pipeline (onde times novos entram);
    # rodar em toda visita à home era um full scan de teams.all() + commit à toa.

    active_comps = db.query(Competition).filter_by(is_active=True).order_by(Competition.code).all()
    # Default = Todos (visão geral). Filtro opcional via ?comp=
    filter_code = (comp_code or "").strip().upper() or None
    if filter_code == "ALL":
        filter_code = None
    codes = resolve_competition_codes(db, competition_code=filter_code)

    today = get_today_context()
    analyses = analyze_upcoming(db, limit=80, for_today_only=True, competition_code=filter_code)
    attach_saved_reports(db, analyses)
    candidates = [a for a in analyses if not a.excluded]
    discarded = [a for a in analyses if a.excluded]
    profiles_ready, profiles_total = count_teams_with_profiles(db, competition_code=filter_code)
    today_count = count_today_fixtures(db, competition_code=filter_code)
    upcoming_count = count_upcoming_fixtures(db, competition_code=filter_code)

    report_q = db.query(FixtureReport).join(Fixture)
    if filter_code:
        report_q = report_q.filter(Fixture.competition_code == filter_code)
    elif codes:
        report_q = report_q.filter(Fixture.competition_code.in_(codes))
    last_report = report_q.order_by(FixtureReport.analyzed_at.desc()).first()
    last_analysis_at = last_report.analyzed_at if last_report else None

    # Odds: merge cache de todas as comps no escopo (badge/odds por jogo usa fixture.comp)
    odds_list: list = []
    odds_comps = [filter_code] if filter_code else (codes or active_competition_codes(db))
    for code in odds_comps:
        comp = db.query(Competition).filter_by(code=code).first()
        if not comp or not comp.odds_json:
            continue
        try:
            odds_list.extend(json.loads(comp.odds_json) or [])
        except Exception:
            pass

    from palpitaria.services.pipeline_trigger import pipeline_used_today
    from palpitaria.services.chat_service import load_competition_odds_games, match_odds_in_games
    from palpitaria.services.strategy_card import enrich_strategy_card_display_mode

    # Cache por competição: 1 query + 1 json.loads por comp, não por jogo exibido.
    odds_games_cache: dict[str, list | None] = {}
    for item in analyses:
        item_comp = item.competition_code or filter_code or "BSA"
        if item_comp not in odds_games_cache:
            odds_games_cache[item_comp] = load_competition_odds_games(db, item_comp)
        enrich_strategy_card_display_mode(
            item,
            match_odds_in_games(odds_games_cache[item_comp], item.home_name, item.away_name),
        )

    gate_comp = filter_code or "ALL"
    pipeline_used, pipeline_today_run = pipeline_used_today(db, gate_comp)
    pipeline_running_here = PIPELINE_STATE["running"] and (
        PIPELINE_STATE.get("comp") == gate_comp
        or (gate_comp == "ALL" and PIPELINE_STATE.get("comp") == "ALL")
    )

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
            "current_comp": filter_code or "",
            "betfair_odds": odds_list,
            "user": user,
            "pipeline_used_today": pipeline_used,
            "pipeline_running": pipeline_running_here,
            "pipeline_today_run": pipeline_today_run,
            "pipeline_comp": gate_comp,
        },
    )


@router.get("/", response_class=HTMLResponse)
def home(request: Request, comp: str | None = None, db: Session = Depends(get_db), user=Depends(login_required)) -> HTMLResponse:
    return _render_home(request, db, user, comp_code=comp)


@router.post("/sync", response_class=HTMLResponse)
def sync_data(request: Request, comp: str | None = None, db: Session = Depends(get_db), user=Depends(admin_required)) -> HTMLResponse:
    from palpitaria.services.config_service import get_api_config
    token = get_api_config(db, "FOOTBALL_DATA_TOKEN")
    if not token:
        raise HTTPException(status_code=400, detail="Configure FOOTBALL_DATA_TOKEN no Admin ou .env")

    scope = _comp_scope(comp)
    codes = _codes_for_scope(db, scope)
    ingest_result = {"fixtures": 0, "teams": 0}
    try:
        LOG_BUFFER.clear()
        add_log(f"[PASSO 1] Sincronizando jogos — escopo {scope} ({', '.join(codes) or 'nenhum'})...")
        client = FootballDataClient(token=token)
        for code in codes:
            add_log(f"  → {code}")
            part = ingest_competition(db, client, competition_code=code, log_callback=add_log)
            ingest_result["fixtures"] += int(part.get("fixtures") or 0)
            ingest_result["teams"] += int(part.get("teams") or 0)
            resolved = resolve_pending_recommendations(db, code)
            if resolved:
                add_log(f"IA ({code}): {resolved} recomendação(ões) conferidas.")
        renamed = localize_existing_teams(db)
        if renamed:
            add_log(f"Nomes padronizados PT-BR: {renamed} seleções")
        add_log(f"Concluído: {ingest_result.get('fixtures', 0)} jogos, {ingest_result.get('teams', 0)} seleções.")
    except FootballDataError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except Exception as exc:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Erro na sincronização: {exc}") from exc

    if request.headers.get("HX-Request"):
        return HTMLResponse(
            "",
            status_code=200,
            headers={"HX-Redirect": _home_redirect_for_scope(scope)},
        )

    return TEMPLATES.TemplateResponse(
        request,
        "sync_result.html",
        {
            "ingest": ingest_result,
            "profiles": None,
            "message": f"Jogos sincronizados ({scope}). No dia do jogo, clique em Atualizar Tudo.",
            "redirect": True,
        },
    )


@router.post("/sync-profiles", response_class=HTMLResponse)
def sync_profiles(request: Request, comp: str | None = None, db: Session = Depends(get_db), user=Depends(admin_required)) -> HTMLResponse:
    from palpitaria.services.config_service import get_api_config
    token = get_api_config(db, "FOOTBALL_DATA_TOKEN")
    if not token:
        raise HTTPException(status_code=400, detail="Configure FOOTBALL_DATA_TOKEN no Admin ou .env")

    scope = _comp_scope(comp)
    codes = _codes_for_scope(db, scope)
    profiles = 0
    try:
        LOG_BUFFER.clear()
        add_log(f"[PASSO 2] Atualizando perfis API — hoje ({scope})...")
        client = FootballDataClient(token=token)
        for code in codes:
            profiles += build_team_profiles(
                db,
                client,
                log_callback=add_log,
                competition_code=code,
                today_only=True,
            )
        ready, total = count_teams_with_profiles(db, competition_code=None if scope == "ALL" else scope)
        today_ctx = get_today_context()
        add_log(f"Concluído: {profiles} perfil(is) hoje. Escopo {scope}: {ready}/{total}.")
    except FootballDataError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except Exception as exc:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Erro nos perfis: {exc}") from exc

    if request.headers.get("HX-Request"):
        return HTMLResponse(
            "",
            status_code=200,
            headers={"HX-Redirect": _home_redirect_for_scope(scope)},
        )

    return TEMPLATES.TemplateResponse(
        request,
        "sync_result.html",
        {
            "ingest": None,
            "profiles": profiles,
            "message": (
                f"Perfis API: {profiles} seleção(ões) hoje ({today_ctx.label}). "
                f"Escopo {scope}: {ready}/{total}."
            ),
            "redirect": True,
        },
    )


@router.post("/analyze")
def run_analysis(request: Request, comp: str | None = None, db: Session = Depends(get_db), user=Depends(admin_required)):
    LOG_BUFFER.clear()
    scope = _comp_scope(comp)
    for code in _codes_for_scope(db, scope):
        _execute_analysis_pipeline(db, code)

    if request.headers.get("HX-Request"):
        return HTMLResponse(
            "",
            status_code=200,
            headers={"HX-Redirect": _home_redirect_for_scope(scope)},
        )
    return RedirectResponse(url=_home_redirect_for_scope(scope), status_code=303)


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

    from palpitaria.services.chat_service import _odds_for_match

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
        from palpitaria.services.knockout_climate import enrich_analysis_knockout

        enrich_analysis_knockout(analysis)

        add_log("  [3/3] Decisão + cartão de estratégias (LLM)...")
        settings.openai_api_key = llm_key
        settings.openai_base_url = llm_base

        analysis.best_pick = refine_best_pick(analysis)
        # Narrativa única (cartão + comentário) — modelo já decidiu o pick
        from palpitaria.services.narrate import narrate_fixture

        odds = _odds_for_match(db, analysis.home_name, analysis.away_name, comp_code)
        # Descartados totais sem pick: zero token
        if analysis.best_pick is None:
            analysis.strategy_card = None
            analysis.llm_explanation = None
        else:
            card, comment = narrate_fixture(analysis, odds=odds)
            analysis.strategy_card = card
            analysis.llm_explanation = comment
        persist_analysis(db, analysis, analysis.llm_explanation or "", competition_code=comp_code)
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


@router.post("/pipeline", response_class=HTMLResponse)
def run_full_pipeline(request: Request, comp: str | None = None, db: Session = Depends(get_db), user=Depends(admin_required)):
    from palpitaria.services.config_service import get_api_config
    from palpitaria.services.pipeline_trigger import claim_daily_pipeline_run, finalize_pipeline_run

    scope = _comp_scope(comp)
    if PIPELINE_STATE["running"]:
        msg = "Já há uma atualização em andamento. Aguarde terminar."
        if request.headers.get("HX-Request"):
            return HTMLResponse(f'<div class="alert">{msg}</div>', status_code=409)
        raise HTTPException(status_code=409, detail=msg)

    try:
        run, _ = claim_daily_pipeline_run(db, scope, trigger="web_admin")
    except HTTPException as exc:
        if request.headers.get("HX-Request"):
            return HTMLResponse(f'<div class="alert">🔒 {exc.detail}</div>', status_code=exc.status_code)
        raise

    football_token = get_api_config(db, "FOOTBALL_DATA_TOKEN")
    if not football_token:
        finalize_pipeline_run(db, run.id, error="FOOTBALL_DATA_TOKEN não configurado")
        raise HTTPException(status_code=400, detail="Configure FOOTBALL_DATA_TOKEN no Admin ou .env")

    try:
        _start_pipeline(scope, db, football_token, run_id=run.id)
    except HTTPException:
        finalize_pipeline_run(db, run.id, error="Falha ao iniciar pipeline")
        raise
    except Exception as exc:
        finalize_pipeline_run(db, run.id, error=str(exc))
        raise HTTPException(status_code=500, detail="Falha ao iniciar pipeline") from exc

    if request.headers.get("HX-Request"):
        return HTMLResponse("", status_code=202)
    return RedirectResponse(url=_home_redirect_for_scope(scope), status_code=303)


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
    scope = _comp_scope(comp_code)
    codes = _codes_for_scope(db, scope)
    add_log(f"🚀 INICIANDO PIPELINE COMPLETO ({scope}) — {', '.join(codes) or 'nenhum ativo'}")

    try:
        if _pipeline_aborted():
            raise RuntimeError("Pipeline abortado")

        token = get_api_config(db, "FOOTBALL_DATA_TOKEN")
        if not token:
            raise RuntimeError("Configure FOOTBALL_DATA_TOKEN no Admin ou .env")

        if not codes:
            add_log("AVISO: nenhuma competição ativa para processar.")
        else:
            client = FootballDataClient(token=token)
            for idx, code in enumerate(codes, start=1):
                if _pipeline_aborted():
                    raise RuntimeError("Pipeline abortado")
                add_log(f"\n===== {code} ({idx}/{len(codes)}) =====")

                add_log(f"[PASSO 1/3] Sincronizando jogos ({code})...")
                ingest_result = ingest_competition(db, client, competition_code=code, log_callback=add_log)
                localize_existing_teams(db)
                resolve_pending_recommendations(db, code)
                add_log(f"✓ {code}: {ingest_result.get('fixtures', 0)} jogos sync.")

                if _pipeline_aborted():
                    raise RuntimeError("Pipeline abortado")

                add_log(f"[PASSO 2/3] Perfis API ({code})...")
                profiles = build_team_profiles(
                    db,
                    client,
                    log_callback=add_log,
                    competition_code=code,
                    today_only=True,
                )
                ready, total = count_teams_with_profiles(db, code)
                add_log(f"✓ {code}: {profiles} perfis hoje · {ready}/{total} prontos.")

                if _pipeline_aborted():
                    raise RuntimeError("Pipeline abortado")

                add_log(f"[PASSO 3/3] Leituras IA ({code})...")
                _execute_analysis_pipeline(db, code)

                add_log(f"[PASSO 4] Odds ({code})...")
                update_competition_odds(db, code)

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


@router.post("/api/v1/pipeline/trigger")
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

    comp_code = _comp_scope(comp)
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


@router.get("/api/v1/pipeline/status")
def api_pipeline_status(t: str, db: Session = Depends(get_db)) -> dict:
    from palpitaria.services.pipeline_trigger import get_run_by_watch_token, run_status_payload

    run = get_run_by_watch_token(db, t)
    if run is None:
        raise HTTPException(status_code=404, detail="Token inválido ou expirado.")
    return run_status_payload(run)


@router.get("/api/v1/pipeline/logs")
def api_pipeline_logs(t: str, db: Session = Depends(get_db)) -> HTMLResponse:
    from palpitaria.services.pipeline_trigger import fetch_log_lines, get_run_by_watch_token

    run = get_run_by_watch_token(db, t)
    if run is None:
        raise HTTPException(status_code=404, detail="Token inválido ou expirado.")
    content = "\n".join(fetch_log_lines(db, run.id))
    return HTMLResponse(f"<pre>{content}</pre>")


@router.get("/pipeline/watch", response_class=HTMLResponse)
def pipeline_watch_page(request: Request, t: str | None = None):
    return TEMPLATES.TemplateResponse(
        request,
        "pipeline_watch.html",
        {
            "watch_token": t or "",
            "app_timezone": settings.app_timezone,
        },
    )


@router.post("/pipeline/abort")
def abort_pipeline(user=Depends(admin_required)) -> dict:
    was_active = PIPELINE_STATE["active"] or PIPELINE_STATE["running"]
    PIPELINE_CANCEL.set()  # Sinaliza para a thread parar
    LOG_BUFFER.clear()
    reset_pipeline_state(cancelled=was_active)
    return {"aborted": was_active, "status": "idle"}


@router.get("/pipeline/status")
def pipeline_status(user=Depends(admin_required)) -> dict:
    return {
        "active": PIPELINE_STATE["active"],
        "running": PIPELINE_STATE["running"],
        "done": PIPELINE_STATE["done"],
        "error": PIPELINE_STATE["error"],
        "comp": PIPELINE_STATE["comp"],
        "cancelled": PIPELINE_STATE["cancelled"],
    }


@router.get("/logs")
def get_logs(user=Depends(admin_required)):
    content = "\n".join(LOG_BUFFER)
    return HTMLResponse(f"<pre>{content}</pre>")

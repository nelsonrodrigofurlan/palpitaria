"""Disparo remoto seguro do pipeline — HMAC, trava 1x/dia, token de acompanhamento."""

from __future__ import annotations

import hashlib
import hmac
import secrets
import time
from datetime import datetime

from fastapi import HTTPException, Request
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from palpitaria.config import settings
from palpitaria.models import Fixture, FixtureReport, PipelineLogLine, PipelineRun, RemotePipelineDaily
from palpitaria.services.analyzer import get_today_context

TRIGGER_PATH = "/api/v1/pipeline/trigger"
MAX_TIMESTAMP_SKEW_SEC = 300


def today_run_day() -> str:
    return get_today_context().date_local.isoformat()


def hash_watch_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def verify_trigger_request(request: Request) -> None:
    secret = settings.pipeline_trigger_secret
    if not secret:
        raise HTTPException(status_code=503, detail="Disparo remoto não configurado no servidor.")

    timestamp = request.headers.get("X-Pipeline-Timestamp", "").strip()
    signature = request.headers.get("X-Pipeline-Signature", "").strip().lower()
    if not timestamp or not signature:
        raise HTTPException(status_code=401, detail="Não autorizado.")

    try:
        ts = int(timestamp)
    except ValueError as exc:
        raise HTTPException(status_code=401, detail="Não autorizado.") from exc

    if abs(time.time() - ts) > MAX_TIMESTAMP_SKEW_SEC:
        raise HTTPException(status_code=401, detail="Não autorizado.")

    payload = f"{timestamp}\nPOST\n{TRIGGER_PATH}\n"
    expected = hmac.new(secret.encode("utf-8"), payload.encode("utf-8"), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, signature):
        raise HTTPException(status_code=401, detail="Não autorizado.")


def pipeline_used_today(db: Session, comp_code: str) -> tuple[bool, PipelineRun | None]:
    """Indica se o pipeline completo já rodou hoje para este campeonato."""
    run_day = today_run_day()
    comp = comp_code or settings.world_cup_code
    lock = (
        db.query(RemotePipelineDaily)
        .filter_by(run_day=run_day, comp_code=comp)
        .one_or_none()
    )
    if lock is not None:
        run = db.query(PipelineRun).filter(PipelineRun.id == lock.pipeline_run_id).one_or_none()
        return True, run

    run = (
        db.query(PipelineRun)
        .filter(PipelineRun.run_day == run_day, PipelineRun.comp_code == comp)
        .order_by(PipelineRun.started_at.desc())
        .first()
    )
    if run is not None:
        return True, run

    # Execuções web antigas (antes da trava) — leituras gravadas hoje neste campeonato
    ctx = get_today_context()
    has_analysis_today = (
        db.query(FixtureReport.id)
        .join(Fixture, FixtureReport.fixture_id == Fixture.id)
        .filter(Fixture.competition_code == comp)
        .filter(FixtureReport.analyzed_at >= ctx.start_utc)
        .filter(FixtureReport.analyzed_at < ctx.end_utc)
        .first()
    )
    if has_analysis_today:
        return True, None

    return False, None


_DAILY_LIMIT_MSG = (
    "Atualização completa já executada hoje para {comp} ({run_day}). "
    "Só é permitido 1 vez por dia por campeonato."
)


def claim_daily_pipeline_run(
    db: Session,
    comp_code: str,
    *,
    trigger: str,
) -> tuple[PipelineRun, str | None]:
    """Reserva slot diário por campeonato (web ou remoto). Falha com 429 se já usado hoje."""
    run_day = today_run_day()
    comp = comp_code or settings.world_cup_code
    used, _ = pipeline_used_today(db, comp)
    if used:
        raise HTTPException(
            status_code=429,
            detail=_DAILY_LIMIT_MSG.format(run_day=run_day, comp=comp),
        )

    watch_token = secrets.token_urlsafe(32) if trigger == "remote_api" else None
    run = PipelineRun(
        run_day=run_day,
        trigger=trigger,
        status="running",
        comp_code=comp,
        watch_token_hash=hash_watch_token(watch_token) if watch_token else None,
    )
    db.add(run)
    db.flush()
    db.add(RemotePipelineDaily(run_day=run_day, comp_code=comp, pipeline_run_id=run.id))
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            status_code=429,
            detail=_DAILY_LIMIT_MSG.format(run_day=run_day, comp=comp),
        ) from exc
    db.refresh(run)
    return run, watch_token


def claim_remote_daily_run(db: Session, comp_code: str) -> tuple[PipelineRun, str]:
    """Reserva slot diário remoto. Falha com 429 se já usado hoje."""
    run, watch_token = claim_daily_pipeline_run(db, comp_code, trigger="remote_api")
    assert watch_token is not None
    return run, watch_token


def get_run_by_watch_token(db: Session, token: str) -> PipelineRun | None:
    if not token or len(token) < 20:
        return None
    token_hash = hash_watch_token(token)
    return db.query(PipelineRun).filter(PipelineRun.watch_token_hash == token_hash).one_or_none()


def persist_log_line(db: Session, run_id: int, line: str) -> None:
    db.add(PipelineLogLine(run_id=run_id, line=line))
    db.commit()


def fetch_log_lines(db: Session, run_id: int) -> list[str]:
    rows = (
        db.query(PipelineLogLine)
        .filter(PipelineLogLine.run_id == run_id)
        .order_by(PipelineLogLine.id.asc())
        .all()
    )
    return [row.line for row in rows]


def finalize_pipeline_run(db: Session, run_id: int, *, error: str | None = None) -> None:
    run = db.query(PipelineRun).filter(PipelineRun.id == run_id).one_or_none()
    if run is None:
        return
    run.completed_at = datetime.utcnow()
    if error:
        run.status = "error"
        run.error_message = error
    else:
        run.status = "done"
    db.commit()


def run_status_payload(run: PipelineRun) -> dict:
    return {
        "run_id": run.id,
        "run_day": run.run_day,
        "trigger": run.trigger,
        "active": run.status == "running",
        "running": run.status == "running",
        "done": run.status in ("done", "error"),
        "error": run.error_message,
        "comp": run.comp_code,
        "started_at": run.started_at.isoformat() if run.started_at else None,
        "completed_at": run.completed_at.isoformat() if run.completed_at else None,
    }


def watch_url_for_token(token: str) -> str:
    base = settings.app_url.rstrip("/")
    return f"{base}/pipeline/watch?t={token}"

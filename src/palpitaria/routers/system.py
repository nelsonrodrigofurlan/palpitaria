"""Endpoints de sistema: healthcheck (Cloud Run) e proxy de imagens (CORS)."""

import io
import os

import httpx
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from palpitaria.config import settings
from palpitaria.database import get_db
from palpitaria.services.analyzer import count_today_fixtures, count_upcoming_fixtures

router = APIRouter()


@router.get("/health/live")
def health_live() -> dict:
    """Liveness — Cloud Run só precisa saber que o processo escutou na porta."""
    return {"status": "ok"}


@router.get("/health")
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


@router.get("/proxy-crest")
async def proxy_crest(url: str):
    """Proxy para evitar problemas de CORS ao gerar imagem com html2canvas."""
    if not url:
        raise HTTPException(status_code=400, detail="Missing URL")

    # Permitir apenas domínios conhecidos de escudos para segurança
    allowed_domains = ["crests.football-data.org", "wikipedia.org", "wikimedia.org"]
    if not any(domain in url for domain in allowed_domains):
         raise HTTPException(status_code=403, detail="Unauthorized image domain")

    async with httpx.AsyncClient() as client:
        try:
            # repassa headers básicos (user-agent) para evitar bloqueio
            headers = {"User-Agent": "PalpitariaFC/1.0"}
            resp = await client.get(url, timeout=10.0, headers=headers, follow_redirects=True)
            resp.raise_for_status()

            content_type = resp.headers.get("content-type", "image/png")
            return StreamingResponse(io.BytesIO(resp.content), media_type=content_type)
        except Exception as e:
            raise HTTPException(status_code=502, detail=f"Proxy error: {e}")

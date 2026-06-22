"""Custos operacionais — OpenRouter, APIs e extrato local de LLM."""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import httpx
from sqlalchemy import func
from sqlalchemy.orm import Session

from palpitaria.config import settings
from palpitaria.models import FixtureReport, LlmUsageLog, PipelineRun
from palpitaria.services.config_service import get_api_config
from palpitaria.services.llm_client import uses_openrouter

OPENROUTER_API = "https://openrouter.ai/api/v1"
HTTP_TIMEOUT = 15.0


def _mask_key(key: str) -> str:
    if not key or len(key) < 12:
        return "—"
    return f"{key[:8]}…{key[-4:]}"


def _openrouter_headers(api_key: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {api_key}",
        "HTTP-Referer": settings.app_url,
        "X-Title": "palpitaria",
    }


def _fetch_openrouter_json(path: str, api_key: str, *, params: dict | None = None) -> dict[str, Any]:
    url = f"{OPENROUTER_API}{path}"
    with httpx.Client(timeout=HTTP_TIMEOUT) as client:
        response = client.get(url, headers=_openrouter_headers(api_key), params=params or {})
        if response.status_code == 401:
            return {"error": "Chave inválida ou expirada.", "status_code": 401}
        if response.status_code == 403:
            return {
                "error": "Permissão insuficiente — endpoint exige Management API Key no OpenRouter.",
                "status_code": 403,
            }
        if response.status_code >= 400:
            return {"error": f"HTTP {response.status_code}", "status_code": response.status_code}
        return response.json()


def fetch_openrouter_key_info(api_key: str) -> dict[str, Any]:
    """GET /auth/key — saldo, limites e uso da chave de provisionamento."""
    payload = _fetch_openrouter_json("/auth/key", api_key)
    if "error" in payload:
        return {"ok": False, **payload}
    data = payload.get("data") or {}
    return {
        "ok": True,
        "label": data.get("label"),
        "limit": data.get("limit"),
        "limit_remaining": data.get("limit_remaining"),
        "limit_reset": data.get("limit_reset"),
        "usage_total": data.get("usage"),
        "usage_daily": data.get("usage_daily"),
        "usage_weekly": data.get("usage_weekly"),
        "usage_monthly": data.get("usage_monthly"),
        "is_free_tier": data.get("is_free_tier"),
    }


def fetch_openrouter_credits(api_key: str) -> dict[str, Any]:
    """GET /credits — total comprado vs usado (Management key)."""
    payload = _fetch_openrouter_json("/credits", api_key)
    if "error" in payload:
        return {"ok": False, **payload}
    data = payload.get("data") or {}
    total_credits = float(data.get("total_credits") or 0)
    total_usage = float(data.get("total_usage") or 0)
    return {
        "ok": True,
        "total_credits": total_credits,
        "total_usage": total_usage,
        "remaining": round(total_credits - total_usage, 6),
    }


def fetch_openrouter_activity(api_key: str) -> dict[str, Any]:
    """GET /activity — extrato dos últimos 30 dias UTC (Management key)."""
    payload = _fetch_openrouter_json("/activity", api_key)
    if "error" in payload:
        return {"ok": False, **payload}
    rows = payload.get("data") or []
    by_date: dict[str, dict[str, float | int]] = defaultdict(
        lambda: {"usage_usd": 0.0, "requests": 0, "prompt_tokens": 0, "completion_tokens": 0}
    )
    for row in rows:
        day = row.get("date") or "?"
        bucket = by_date[day]
        bucket["usage_usd"] += float(row.get("usage") or 0)
        bucket["requests"] += int(row.get("requests") or 0)
        bucket["prompt_tokens"] += int(row.get("prompt_tokens") or 0)
        bucket["completion_tokens"] += int(row.get("completion_tokens") or 0)

    daily = [
        {"date": day, **vals, "usage_usd": round(vals["usage_usd"], 6)}
        for day, vals in sorted(by_date.items(), reverse=True)
    ]
    return {
        "ok": True,
        "rows": rows,
        "daily": daily,
        "total_usd": round(sum(d["usage_usd"] for d in daily), 6),
    }


def local_usage_summary(db: Session) -> dict[str, Any]:
    tz = ZoneInfo(settings.app_timezone)
    now_local = datetime.now(tz)
    start_today = now_local.replace(hour=0, minute=0, second=0, microsecond=0).astimezone(ZoneInfo("UTC")).replace(tzinfo=None)
    start_month = now_local.replace(day=1, hour=0, minute=0, second=0, microsecond=0).astimezone(ZoneInfo("UTC")).replace(tzinfo=None)

    base = db.query(LlmUsageLog)
    total_calls = base.count()
    if total_calls == 0:
        legacy_llm = (
            db.query(func.count(FixtureReport.id))
            .filter(FixtureReport.llm_explanation.isnot(None))
            .filter(FixtureReport.llm_explanation != "")
            .scalar()
        ) or 0
        return {
            "has_logs": False,
            "legacy_llm_reports": legacy_llm,
            "total_calls": 0,
            "cost_today_usd": 0.0,
            "cost_month_usd": 0.0,
            "cost_total_usd": 0.0,
            "tokens_total": 0,
            "by_feature": [],
        }

    cost_total = db.query(func.coalesce(func.sum(LlmUsageLog.cost_usd), 0.0)).scalar() or 0.0
    cost_today = (
        db.query(func.coalesce(func.sum(LlmUsageLog.cost_usd), 0.0))
        .filter(LlmUsageLog.created_at >= start_today)
        .scalar()
        or 0.0
    )
    cost_month = (
        db.query(func.coalesce(func.sum(LlmUsageLog.cost_usd), 0.0))
        .filter(LlmUsageLog.created_at >= start_month)
        .scalar()
        or 0.0
    )
    tokens_total = db.query(func.coalesce(func.sum(LlmUsageLog.total_tokens), 0)).scalar() or 0

    by_feature_rows = (
        db.query(
            LlmUsageLog.feature,
            func.count(LlmUsageLog.id),
            func.coalesce(func.sum(LlmUsageLog.cost_usd), 0.0),
            func.coalesce(func.sum(LlmUsageLog.total_tokens), 0),
        )
        .group_by(LlmUsageLog.feature)
        .order_by(func.coalesce(func.sum(LlmUsageLog.cost_usd), 0.0).desc())
        .all()
    )
    by_feature = [
        {
            "feature": row[0],
            "calls": row[1],
            "cost_usd": round(float(row[2] or 0), 6),
            "tokens": int(row[3] or 0),
        }
        for row in by_feature_rows
    ]

    recent = (
        db.query(LlmUsageLog)
        .order_by(LlmUsageLog.created_at.desc())
        .limit(80)
        .all()
    )

    return {
        "has_logs": True,
        "legacy_llm_reports": 0,
        "total_calls": total_calls,
        "cost_today_usd": round(float(cost_today), 6),
        "cost_month_usd": round(float(cost_month), 6),
        "cost_total_usd": round(float(cost_total), 6),
        "tokens_total": int(tokens_total),
        "by_feature": by_feature,
        "recent": recent,
    }


def infra_status(db: Session) -> list[dict[str, Any]]:
    football = get_api_config(db, "FOOTBALL_DATA_TOKEN")
    llm_key = get_api_config(db, "OPENAI_API_KEY")
    llm_base = get_api_config(db, "OPENAI_BASE_URL")

    items = [
        {
            "name": "OpenRouter (LLM)",
            "configured": bool(llm_key and settings.has_llm),
            "billing": "Pay-as-you-go (USD)",
            "note": f"Modelo: {settings.openai_chat_model}",
            "cost_trackable": True,
        },
        {
            "name": "Football-Data.org",
            "configured": bool(football and settings.has_football_token),
            "billing": "Free tier / plano gratuito",
            "note": "Sincronização de jogos e perfis API",
            "cost_trackable": False,
        },
        {
            "name": "The Odds API",
            "configured": settings.has_odds_api,
            "billing": "Plano por créditos (se configurado)",
            "note": "Odds externas — sem extrato automático ainda",
            "cost_trackable": False,
        },
        {
            "name": "Betfair",
            "configured": settings.has_betfair,
            "billing": "Comissão sobre greens (P&L nas filiais)",
            "note": "Sem API oficial no produto",
            "cost_trackable": False,
        },
        {
            "name": "Supabase / Hosting",
            "configured": settings.uses_postgres,
            "billing": "Infra externa (fora do app)",
            "note": f"DB: {settings.db_host_label}",
            "cost_trackable": False,
        },
    ]
    if llm_base:
        items[0]["note"] += f" · Base: {llm_base}"
    return items


def build_cost_dashboard(db: Session) -> dict[str, Any]:
    llm_key = get_api_config(db, "OPENAI_API_KEY")
    provider = settings.llm_provider_label
    is_or = uses_openrouter() or provider == "openrouter"

    openrouter: dict[str, Any] = {
        "configured": bool(llm_key and settings.has_llm and is_or),
        "key_masked": _mask_key(llm_key) if llm_key else "—",
        "model": settings.openai_chat_model,
    }
    if openrouter["configured"]:
        openrouter["key_info"] = fetch_openrouter_key_info(llm_key)
        openrouter["credits"] = fetch_openrouter_credits(llm_key)
        openrouter["activity"] = fetch_openrouter_activity(llm_key)
    else:
        openrouter["key_info"] = {"ok": False, "error": "LLM não configurado ou não é OpenRouter."}
        openrouter["credits"] = {"ok": False}
        openrouter["activity"] = {"ok": False}

    local = local_usage_summary(db)
    pipelines_month = (
        db.query(func.count(PipelineRun.id))
        .filter(PipelineRun.started_at >= datetime.utcnow() - timedelta(days=30))
        .scalar()
        or 0
    )

    key_info = openrouter.get("key_info") or {}
    monthly_usd = key_info.get("usage_monthly") if key_info.get("ok") else local["cost_month_usd"]

    return {
        "provider": provider,
        "openrouter": openrouter,
        "local": local,
        "infra": infra_status(db),
        "pipelines_last_30d": pipelines_month,
        "summary": {
            "balance_usd": key_info.get("limit_remaining") if key_info.get("ok") else None,
            "spent_today_usd": key_info.get("usage_daily") if key_info.get("ok") else local["cost_today_usd"],
            "spent_month_usd": monthly_usd,
            "spent_total_usd": key_info.get("usage_total") if key_info.get("ok") else local["cost_total_usd"],
        },
        "fetched_at": datetime.utcnow(),
    }

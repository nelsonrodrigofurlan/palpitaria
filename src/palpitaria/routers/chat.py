"""Chat de inteligência coletiva."""

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from palpitaria.config import settings
from palpitaria.database import get_db
from palpitaria.deps import TEMPLATES, login_required
from palpitaria.services.ledger import current_period, period_label

router = APIRouter()


@router.get("/chat", response_class=HTMLResponse)
def chat_page(request: Request, db: Session = Depends(get_db), user=Depends(login_required)):
    from palpitaria.services.chat_service import fetch_user_chat_history, user_chat_daily_quota

    cy, cm = current_period()
    history = fetch_user_chat_history(db, user.id, ascending=True)
    quota = user_chat_daily_quota(db, user.id, user.is_admin)
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


@router.post("/chat/send", response_class=HTMLResponse)
async def chat_send(request: Request, db: Session = Depends(get_db), user=Depends(login_required)):
    from palpitaria.services.chat_service import process_user_message, user_chat_daily_quota
    from palpitaria.services.config_service import get_api_config

    quota = user_chat_daily_quota(db, user.id, user.is_admin)
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
    quota_after = user_chat_daily_quota(db, user.id, user.is_admin)

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

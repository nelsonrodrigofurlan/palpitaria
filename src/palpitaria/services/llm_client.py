from __future__ import annotations

from openai import OpenAI

from palpitaria.config import settings

_client: OpenAI | None = None

OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"


def is_openrouter_key(key: str) -> bool:
    return key.startswith("sk-or-")


def uses_openrouter() -> bool:
    key = settings.openai_api_key
    base = settings.openai_base_url or ""
    return is_openrouter_key(key) or "openrouter.ai" in base


def get_llm_client() -> OpenAI:
    """OpenAI-compatible client — same pattern as SpeakFlow openaiClient.ts."""
    global _client
    if not settings.has_llm:
        raise RuntimeError("OPENAI_API_KEY is not configured")

    if _client is None:
        base_url = settings.openai_base_url
        if not base_url and is_openrouter_key(settings.openai_api_key):
            base_url = OPENROUTER_BASE_URL

        kwargs: dict = {"api_key": settings.openai_api_key}
        if base_url:
            kwargs["base_url"] = base_url

        default_headers = None
        if base_url and "openrouter.ai" in base_url:
            default_headers = {
                "HTTP-Referer": settings.app_url,
                "X-Title": "palpitaria",
            }

        _client = OpenAI(**kwargs, default_headers=default_headers)

    return _client


def resolve_model(model: str | None = None) -> str:
    """OpenRouter requires provider/model (e.g. google/gemini-flash-1.5)."""
    chosen = model or settings.openai_chat_model
    if uses_openrouter() and "/" not in chosen:
        return f"openai/{chosen}"
    return chosen


def chat_completion(
    system: str,
    user: str,
    *,
    temperature: float = 0.4,
    max_tokens: int = 800,
    feature: str = "general",
) -> str:
    client = get_llm_client()
    model = resolve_model()
    try:
        response = client.chat.completions.create(
            model=model,
            temperature=temperature,
            max_tokens=max_tokens,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        )
        content = response.choices[0].message.content or ""
        _persist_usage_log(model=model, feature=feature, response=response)
        return content.strip()
    except Exception as exc:
        _persist_usage_log(model=model, feature=feature, error=str(exc)[:300])
        raise


def chat_completion_json(
    system: str,
    user: str,
    *,
    temperature: float = 0.1,
    max_tokens: int = 500,
    feature: str = "agent_planner",
) -> tuple[str, dict[str, int]]:
    """JSON-oriented completion; returns (content, token_usage)."""
    client = get_llm_client()
    model = resolve_model()
    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]
    kwargs: dict = {
        "model": model,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "messages": messages,
    }
    try:
        try:
            response = client.chat.completions.create(
                **kwargs,
                response_format={"type": "json_object"},
            )
        except Exception:
            response = client.chat.completions.create(**kwargs)
        content = (response.choices[0].message.content or "").strip()
        prompt = completion = total = 0
        usage = getattr(response, "usage", None)
        if usage is not None:
            prompt = int(getattr(usage, "prompt_tokens", None) or 0)
            completion = int(getattr(usage, "completion_tokens", None) or 0)
            total = int(getattr(usage, "total_tokens", None) or prompt + completion)
        _persist_usage_log(model=model, feature=feature, response=response)
        return content, {"prompt": prompt, "completion": completion, "total": total}
    except Exception as exc:
        _persist_usage_log(model=model, feature=feature, error=str(exc)[:300])
        raise


def _extract_usage(response) -> tuple[int, int, int, float | None, str | None]:
    prompt = completion = total = 0
    cost: float | None = None
    generation_id = getattr(response, "id", None)
    usage = getattr(response, "usage", None)
    if usage is not None:
        prompt = int(getattr(usage, "prompt_tokens", None) or 0)
        completion = int(getattr(usage, "completion_tokens", None) or 0)
        total = int(getattr(usage, "total_tokens", None) or prompt + completion)
        raw_cost = getattr(usage, "cost", None)
        if raw_cost is not None:
            cost = float(raw_cost)
    if cost is None and hasattr(response, "model_dump"):
        dump = response.model_dump()
        usage_dict = dump.get("usage") or {}
        cost_val = usage_dict.get("cost")
        if cost_val is not None:
            cost = float(cost_val)
        if not total:
            prompt = int(usage_dict.get("prompt_tokens") or 0)
            completion = int(usage_dict.get("completion_tokens") or 0)
            total = int(usage_dict.get("total_tokens") or prompt + completion)
    return prompt, completion, total, cost, generation_id


def _persist_usage_log(
    *,
    model: str,
    feature: str,
    response=None,
    error: str | None = None,
) -> None:
    try:
        from palpitaria.database import SessionLocal
        from palpitaria.models import LlmUsageLog

        prompt = completion = total = 0
        cost: float | None = None
        generation_id: str | None = None
        if response is not None:
            prompt, completion, total, cost, generation_id = _extract_usage(response)

        db = SessionLocal()
        db.add(
            LlmUsageLog(
                provider="openrouter" if uses_openrouter() else settings.llm_provider_label,
                model=model,
                feature=feature,
                prompt_tokens=prompt,
                completion_tokens=completion,
                total_tokens=total,
                cost_usd=cost,
                generation_id=generation_id,
                success=error is None,
                error_message=error,
            )
        )
        db.commit()
        db.close()
    except Exception:
        pass


def llm_config_hint(error: Exception) -> str:
    message = str(error)
    if "invalid_api_key" in message or "Incorrect API key" in message:
        return "Chave inválida. Use OpenAI (sk-...) ou OpenRouter (sk-or-...) no OPENAI_API_KEY."
    if "OPENAI_API_KEY is not configured" in message:
        return "OPENAI_API_KEY ausente no .env."
    return "Falha ao gerar explicação via LLM."

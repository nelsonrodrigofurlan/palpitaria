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


def chat_completion(system: str, user: str, *, temperature: float = 0.4, max_tokens: int = 800) -> str:
    client = get_llm_client()
    response = client.chat.completions.create(
        model=resolve_model(),
        temperature=temperature,
        max_tokens=max_tokens,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
    )
    content = response.choices[0].message.content
    return content.strip() if content else ""


def llm_config_hint(error: Exception) -> str:
    message = str(error)
    if "invalid_api_key" in message or "Incorrect API key" in message:
        return "Chave inválida. Use OpenAI (sk-...) ou OpenRouter (sk-or-...) no OPENAI_API_KEY."
    if "OPENAI_API_KEY is not configured" in message:
        return "OPENAI_API_KEY ausente no .env."
    return "Falha ao gerar explicação via LLM."

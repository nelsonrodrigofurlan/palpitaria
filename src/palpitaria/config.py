from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse
import os

from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


def _strip_env(value: object) -> object:
    if isinstance(value, str):
        return value.strip().strip('"').strip("'")
    return value


def _is_cloud_run() -> bool:
    return bool(os.getenv("K_SERVICE"))


def _get_env(*names: str) -> str | None:
    """Lê variável de ambiente tolerando espaços acidentais no nome (ex.: 'DATABASE_URL ')."""
    normalized = {key.strip(): value for key, value in os.environ.items()}
    for name in names:
        raw = normalized.get(name)
        if raw and str(raw).strip():
            return str(_strip_env(raw))
    return None


def _resolve_database_url() -> str | None:
    """Lê URL do Postgres direto do ambiente (Cloud Run runtime)."""
    return _get_env("DATABASE_URL", "DATABASE_URI", "SUPABASE_DATABASE_URL", "SUPABASE_DB_URL")


def _database_env_diagnostics() -> dict:
    keys = ("DATABASE_URL", "DATABASE_URI", "SUPABASE_DATABASE_URL", "SUPABASE_DB_URL")
    normalized = {key.strip(): key for key in os.environ}
    result = {}
    for key in keys:
        actual = normalized.get(key)
        result[key] = {
            "present": actual is not None,
            "length": len(os.environ.get(actual, "")) if actual else 0,
            "env_key_used": actual if actual and actual != key else None,
        }
    return result


# Cloud Run: variáveis vêm só do runtime (aba Variáveis do serviço), nunca do build.
_ENV_FILE = None if _is_cloud_run() else ".env"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=_ENV_FILE,
        env_file_encoding="utf-8",
        extra="ignore",
    )

    football_data_token: str = ""

    # LLM — same convention as SpeakFlow (OpenAI SDK + optional OpenRouter)
    openai_api_key: str = ""
    openai_base_url: str = ""
    openai_chat_model: str = "~google/gemini-flash-latest"
    app_url: str = "http://localhost:8000"

    database_url: str = "sqlite:///./data/palpitaria.db"
    debug: bool = False

    @field_validator(
        "database_url",
        "openai_api_key",
        "football_data_token",
        "openai_base_url",
        "app_url",
        mode="before",
    )
    @classmethod
    def strip_quotes(cls, value: object) -> object:
        return _strip_env(value)

    @model_validator(mode="before")
    @classmethod
    def inject_database_url_from_env(cls, data: object) -> object:
        payload = dict(data) if isinstance(data, dict) else {}
        resolved = _resolve_database_url()
        if resolved:
            payload["database_url"] = resolved
        return payload

    @property
    def config_source(self) -> str:
        return "cloud_run_env" if _is_cloud_run() else "local_env_file"

    @property
    def db_url(self) -> str:
        url = self.database_url
        if url.startswith("postgres://"):
            url = url.replace("postgres://", "postgresql://", 1)
        if "supabase.co" in url and "sslmode=" not in url:
            parsed = urlparse(url)
            query = parse_qs(parsed.query)
            query["sslmode"] = ["require"]
            url = urlunparse(parsed._replace(query=urlencode(query, doseq=True)))
        return url

    @property
    def uses_sqlite(self) -> bool:
        return self.db_url.startswith("sqlite")

    @property
    def db_host_label(self) -> str:
        if self.uses_sqlite:
            return "sqlite (local)"
        parsed = urlparse(self.db_url)
        return parsed.hostname or "postgresql"

    @property
    def database_config_error(self) -> str | None:
        """Cloud Run sem DATABASE_URL válida — app sobe, mas /health avisa."""
        if not _is_cloud_run():
            return None
        if self.database_url and not self.uses_sqlite:
            return None

        diag = _database_env_diagnostics()
        if not any(item["present"] and item["length"] > 0 for item in diag.values()):
            typo = next(
                (orig for orig in os.environ if orig.strip() == "DATABASE_URL" and orig != "DATABASE_URL"),
                None,
            )
            if typo:
                return (
                    f"Nome da variável com espaço extra: '{typo}'. "
                    "Renomeie para DATABASE_URL (sem espaço) no Cloud Run."
                )
            return (
                "DATABASE_URL não chegou nesta revisão do Cloud Run. "
                "As outras variáveis (FOOTBALL_DATA_TOKEN, OPENAI_API_KEY) estão OK, "
                "mas DATABASE_URL não está no ambiente do container — confira o YAML da revisão ATIVA. "
                "Se o Cloud Build redeployar, ele pode estar criando revisão sem DATABASE_URL: "
                "edite o serviço, confirme a variável e clique Implantar (sem rebuild)."
            )
        if diag.get("DATABASE_URL", {}).get("present") and diag["DATABASE_URL"]["length"] == 0:
            return "DATABASE_URL existe mas está vazia — apague e recrie a variável no Cloud Run."
        return (
            "DATABASE_URL inválida (não é Postgres). "
            "Use postgresql://postgres:SENHA@db....supabase.co:5432/postgres sem aspas."
        )

    football_data_base_url: str = "https://api.football-data.org/v4"
    world_cup_code: str = "WC"
    world_cup_season: int = 2026

    # Janela "jogos de hoje" — lesões/expulsões mudam de um dia pro outro
    app_timezone: str = "America/Sao_Paulo"

    min_combined_avg_goals: float = 2.0
    strong_combined_avg_goals: float = 3.5
    max_zero_zero_rate: float = 0.12
    strong_max_zero_zero_rate: float = 0.05
    min_both_score_rate: float = 0.55
    strong_both_score_rate: float = 0.70
    min_over_05_historical_rate: float = 0.88
    strong_over_05_historical_rate: float = 0.95
    min_offense_goals: float = 0.8
    strong_offense_goals: float = 1.5

    # Copa — perfis híbridos API+web sempre; refresh configurável
    wc_web_profile_min_matches: int = 1  # estreias Copa: 1 placar explícito já destrava o filtro
    wc_web_profile_refresh_hours: int = 0  # 0 = refresh every analyze run

    # Leitura LLM exibida no card (comentário inicial)
    llm_explanation_max_chars: int = 1500
    llm_explanation_max_tokens: int = 2000

    @property
    def data_dir(self) -> Path:
        path = Path("data")
        path.mkdir(exist_ok=True)
        return path

    @property
    def has_football_token(self) -> bool:
        return bool(self.football_data_token and self.football_data_token != "your_token_here")

    @property
    def has_llm(self) -> bool:
        key = self.openai_api_key
        if not key or key in ("your_openai_key_here", "your_gemini_key_here"):
            return False
        # Modelo no lugar da chave (erro comum no Cloud Run)
        if key.startswith("~") or key.startswith("google/") or key.startswith("openai/"):
            return False
        return True

    @property
    def llm_provider_label(self) -> str:
        if not self.has_llm:
            return "offline"
        if self.openai_base_url and "openrouter.ai" in self.openai_base_url:
            return "openrouter"
        if self.openai_api_key.startswith("sk-or-"):
            return "openrouter"
        return "openai"


settings = Settings()

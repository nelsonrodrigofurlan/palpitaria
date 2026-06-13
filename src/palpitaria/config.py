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

    @model_validator(mode="after")
    def validate_cloud_run_database(self) -> "Settings":
        if _is_cloud_run() and (not self.database_url or self.uses_sqlite):
            msg = (
                "DATABASE_URL ausente ou inválida no Cloud Run. "
                "Configure em Serviço → Editar → Variáveis de ambiente (runtime), não no Cloud Build."
            )
            raise ValueError(msg)
        return self

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

    football_data_base_url: str = "https://api.football-data.org/v4"
    world_cup_code: str = "WC"
    world_cup_season: int = 2026

    # Janela "jogos de hoje" — lesões/expulsões mudam de um dia pro outro
    app_timezone: str = "America/Sao_Paulo"

    min_combined_avg_goals: float = 2.0
    max_zero_zero_rate: float = 0.12
    min_both_score_rate: float = 0.55
    min_over_05_historical_rate: float = 0.88

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
        return bool(key and key not in ("your_openai_key_here", "your_gemini_key_here"))

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

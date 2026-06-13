from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    football_data_token: str = ""

    # LLM — same convention as SpeakFlow (OpenAI SDK + optional OpenRouter)
    openai_api_key: str = ""
    openai_base_url: str = ""
    openai_chat_model: str = "~google/gemini-flash-latest"
    app_url: str = "http://localhost:8000"

    database_url: str = "sqlite:///./data/palpitaria.db"
    debug: bool = False

    @property
    def db_url(self) -> str:
        url = self.database_url
        if url.startswith("postgres://"):
            # Supabase/Postgres compatibility for SQLAlchemy
            url = url.replace("postgres://", "postgresql://", 1)
        return url

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

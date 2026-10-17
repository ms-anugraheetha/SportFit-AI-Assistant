"""Application settings, read from environment variables (and an optional local .env)."""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


def normalize_database_url(url: str) -> str:
    """Make a provider-supplied URL usable with SQLAlchemy + psycopg 3.

    Hosted Postgres providers usually hand out ``postgres://`` or ``postgresql://`` URLs,
    which SQLAlchemy would otherwise map to the legacy psycopg2 driver.
    """
    if url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://"):]
    if url.startswith("postgresql://"):
        url = "postgresql+psycopg://" + url[len("postgresql://"):]
    return url


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = ""

    hf_token: str = ""
    hf_model: str = "openai/gpt-oss-120b"
    hf_base_url: str = "https://router.huggingface.co/v1"

    # Staff auth (used from Phase 3)
    staff_username: str = "staff"
    staff_password_hash: str = ""
    session_secret: str = ""

    app_env: str = "development"
    cors_origins: str = ""

    # Agent limits
    max_tool_iterations: int = 4
    tool_timeout_seconds: float = 15.0
    llm_timeout_seconds: float = 45.0

    @property
    def sqlalchemy_url(self) -> str:
        if not self.database_url:
            raise RuntimeError("DATABASE_URL is not configured")
        return normalize_database_url(self.database_url)

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()

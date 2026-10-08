"""Settings, read from environment variables (or a .env file). Env vars win over .env."""
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+psycopg2://certgen:certgen@localhost:5432/certgen"
    redis_url: str = "redis://localhost:6379/0"
    storage_dir: str = "storage"
    api_keys: str = "dev-secret-key"  # comma-separated
    public_base_url: str = "http://localhost:8000"  # baked into every QR code
    max_recipients_per_request: int = 5000
    max_retries: int = 3
    retry_backoff_seconds: int = 2
    # A certificate stuck in PROCESSING longer than this is assumed to belong to a dead worker.
    processing_timeout_seconds: int = 300
    # True = Celery runs tasks inline (no Redis, no worker). Used by the tests and "lite mode".
    celery_eager: bool = False

    @property
    def api_key_list(self) -> list[str]:
        return [k.strip() for k in self.api_keys.split(",") if k.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()

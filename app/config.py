"""Configuración centralizada, leída de variables de entorno (o de .env en desarrollo)."""

from datetime import timedelta
from functools import lru_cache
from pathlib import Path
from zoneinfo import ZoneInfo

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Telegram
    telegram_bot_token: SecretStr
    # Se guarda como texto ("123,456") porque pydantic-settings exige JSON para listas.
    allowed_user_ids: str = ""

    # Persistencia
    db_path: Path = Path("data/assistant.db")

    # NLP
    openai_api_key: SecretStr
    openai_model: str
    openai_timeout: float = 30.0

    # Google
    google_client_secret_path: Path = Path("secrets/client_secret.json")
    google_token_path: Path = Path("secrets/token.json")
    google_calendar_id: str = "primary"
    google_tasklist_id: str = "@default"
    google_timeout: float = 30.0
    google_oauth_port: int = 8080
    evento_duracion_min: int = 60

    # Worker
    worker_poll_interval: float = 5.0
    worker_max_attempts: int = 5

    # General
    log_level: str = "INFO"
    timezone: str = "Europe/Madrid"

    @property
    def allowed_ids(self) -> frozenset[int]:
        return frozenset(int(x) for x in self.allowed_user_ids.split(",") if x.strip())

    @property
    def tz(self) -> ZoneInfo:
        return ZoneInfo(self.timezone)

    @property
    def duracion_evento(self) -> timedelta:
        return timedelta(minutes=self.evento_duracion_min)


@lru_cache
def get_settings() -> Settings:
    return Settings()

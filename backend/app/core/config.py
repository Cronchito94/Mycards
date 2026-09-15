"""Configuration applicative, lue depuis l'environnement (fichier .env en local)."""

from functools import lru_cache
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_env: Literal["dev", "test", "prod"] = "dev"
    log_level: str = "info"

    # URL SQLAlchemy complète, ex :
    # postgresql+psycopg://tcg:secret@db:5432/tcg
    database_url: str = Field(
        default="postgresql+psycopg://tcg:tcg@db:5432/tcg",
        description="DSN SQLAlchemy (driver psycopg v3)",
    )

    @property
    def async_database_url(self) -> str:
        """Même DSN, forcé sur le driver async.

        psycopg v3 expose un mode async via le même paquet : le dialecte
        SQLAlchemy `postgresql+psycopg` gère les deux, il suffit d'utiliser
        `create_async_engine`. On normalise ici les DSN écrits à l'ancienne
        (`postgresql://`) pour éviter que SQLAlchemy ne tombe sur psycopg2.
        """
        url = self.database_url
        if url.startswith("postgresql://"):
            url = url.replace("postgresql://", "postgresql+psycopg://", 1)
        return url


@lru_cache
def get_settings() -> Settings:
    """Settings mis en cache : lus une seule fois par process."""
    return Settings()

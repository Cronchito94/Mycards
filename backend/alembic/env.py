"""Environnement Alembic.

Deux partis pris :

1. L'URL de connexion n'est jamais écrite dans alembic.ini : elle vient de
   DATABASE_URL (donc du .env / de l'environnement Docker). Rien à committer.
2. Les migrations tournent en **sync** alors que l'application est async.
   psycopg v3 sert les deux modes, donc on réutilise le même DSN en
   retirant simplement toute mention de driver async. Ça évite d'avoir à
   gérer une boucle asyncio dans Alembic pour rien.
"""

from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from app.core.config import get_settings
from app.db.base import Base

# Importer ici les modules de modèles pour qu'ils soient enregistrés sur
# Base.metadata avant l'autogénération (lot 1) :
# from app import models  # noqa: F401

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)


def _sync_url() -> str:
    url = get_settings().database_url
    # Le dialecte psycopg est utilisable en sync tel quel ; on neutralise
    # seulement un éventuel DSN asyncpg hérité d'une config tierce.
    return url.replace("+asyncpg", "+psycopg")


config.set_main_option("sqlalchemy.url", _sync_url())

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """Génère le SQL sans se connecter (alembic upgrade --sql)."""
    context.configure(
        url=_sync_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,  # détecte les changements de type de colonne
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()

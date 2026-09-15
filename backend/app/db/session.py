"""Moteur et sessions SQLAlchemy async.

L'API est async (FastAPI), donc le moteur applicatif l'est aussi. Alembic,
lui, tourne en sync : il utilise le même DSN mais son propre moteur (voir
`alembic/env.py`).
"""

from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import get_settings

_settings = get_settings()

engine: AsyncEngine = create_async_engine(
    _settings.async_database_url,
    echo=False,
    pool_pre_ping=True,  # recycle les connexions coupées par Postgres
)

SessionLocal = async_sessionmaker(
    bind=engine,
    expire_on_commit=False,
    autoflush=False,
)


async def get_session() -> AsyncGenerator[AsyncSession, None]:
    """Dépendance FastAPI : une session par requête."""
    async with SessionLocal() as session:
        yield session

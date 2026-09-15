"""Point d'entrée FastAPI."""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.routes import cards, catalog, collection, health
from app.core.config import get_settings
from app.db.session import engine

settings = get_settings()
logging.basicConfig(level=settings.log_level.upper())
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Démarrage de l'API (env=%s)", settings.app_env)
    yield
    # Fermeture propre du pool : évite les connexions orphelines côté Postgres
    await engine.dispose()
    logger.info("Arrêt de l'API")


API_PREFIX = "/api/v1"

app = FastAPI(
    title="TCG Collection API",
    version="0.1.0",
    description="Gestionnaire de collection de cartes TCG (multi-jeux).",
    lifespan=lifespan,
)

app.include_router(health.router)

# Les routes métier sont préfixées et versionnées : le front du lot 6 et les
# clients tiers doivent pouvoir suivre une évolution de contrat sans casse.
app.include_router(catalog.router, prefix=API_PREFIX)
app.include_router(cards.router, prefix=API_PREFIX)
app.include_router(collection.router, prefix=API_PREFIX)


@app.get("/", include_in_schema=False)
async def root() -> dict[str, str]:
    return {"service": "tcg-collection-api", "docs": "/docs", "health": "/health"}

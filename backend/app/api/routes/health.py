"""Endpoint de santé : sert de critère de fin du lot 0."""

import logging

from fastapi import APIRouter, Depends, Response, status
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_session

logger = logging.getLogger(__name__)

router = APIRouter(tags=["health"])


class HealthResponse(BaseModel):
    status: str  # "ok" | "degraded"
    api: str
    database: str


@router.get("/health", response_model=HealthResponse)
async def health(
    response: Response,
    session: AsyncSession = Depends(get_session),
) -> HealthResponse:
    """Vérifie que l'API répond ET que Postgres est joignable.

    On renvoie 503 si la base est injoignable : un /health qui répond 200
    alors que la base est tombée n'a aucune valeur pour une sonde.
    """
    database = "ok"
    try:
        await session.execute(text("SELECT 1"))
    except Exception as exc:  # noqa: BLE001 — on veut remonter toute panne DB
        logger.warning("Health check DB en échec: %s", exc)
        database = "unreachable"
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE

    return HealthResponse(
        status="ok" if database == "ok" else "degraded",
        api="ok",
        database=database,
    )

"""Référentiel : jeux, extensions et vocabulaires.

Ces endpoints servent à peupler les listes de filtres du front (lot 6). Ils
renvoient des `code` : ce sont eux qu'on repasse aux filtres de `/cards`.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.db.session import get_session
from app.models import Expansion, Finish, Rarity, Tcg
from app.models.tcg import CardType
from app.schemas.catalog import ExpansionOut, TcgOut, VocabularyOut

router = APIRouter(tags=["référentiel"])


@router.get("/tcgs", response_model=list[TcgOut], summary="Jeux disponibles")
async def list_tcgs(session: AsyncSession = Depends(get_session)) -> list[Tcg]:
    stmt = select(Tcg).order_by(Tcg.code)
    return list((await session.execute(stmt)).scalars())


@router.get(
    "/expansions",
    response_model=list[ExpansionOut],
    summary="Extensions, les plus récentes d'abord",
)
async def list_expansions(
    tcg: str | None = Query(None, description="Code du TCG, ex. `pokemon`."),
    session: AsyncSession = Depends(get_session),
) -> list[ExpansionOut]:
    stmt = (
        select(Expansion)
        .options(selectinload(Expansion.names))
        .order_by(Expansion.release_date.desc().nullslast(), Expansion.code)
    )
    if tcg:
        stmt = stmt.join(Tcg, Tcg.id == Expansion.tcg_id).where(Tcg.code == tcg)
    extensions = (await session.execute(stmt)).scalars().unique()
    return [ExpansionOut.from_expansion(e) for e in extensions]


async def _vocabulary(
    session: AsyncSession, model: type, tcg: str | None
) -> list[VocabularyOut]:
    stmt = select(model).order_by(model.sort_order.nullslast(), model.code)
    if tcg:
        stmt = stmt.join(Tcg, Tcg.id == model.tcg_id).where(Tcg.code == tcg)
    return [VocabularyOut.model_validate(x) for x in (await session.execute(stmt)).scalars()]


@router.get("/rarities", response_model=list[VocabularyOut], summary="Raretés")
async def list_rarities(
    tcg: str | None = Query(None),
    session: AsyncSession = Depends(get_session),
) -> list[VocabularyOut]:
    return await _vocabulary(session, Rarity, tcg)


@router.get("/finishes", response_model=list[VocabularyOut], summary="Finitions")
async def list_finishes(
    tcg: str | None = Query(None),
    session: AsyncSession = Depends(get_session),
) -> list[VocabularyOut]:
    return await _vocabulary(session, Finish, tcg)


@router.get("/card-types", response_model=list[VocabularyOut], summary="Types de carte")
async def list_card_types(
    tcg: str | None = Query(None),
    session: AsyncSession = Depends(get_session),
) -> list[VocabularyOut]:
    return await _vocabulary(session, CardType, tcg)

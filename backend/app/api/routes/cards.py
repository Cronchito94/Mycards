"""Recherche et détail des cartes."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_session
from app.schemas.catalog import CardDetail, CardSummary
from app.schemas.common import Page
from app.services import search as search_service
from app.services.search import MIN_QUERY_LENGTH, CardFilters

router = APIRouter(prefix="/cards", tags=["cartes"])


@router.get(
    "",
    response_model=Page[CardSummary],
    summary="Rechercher des cartes",
    description=(
        "Recherche par nom, **toutes langues confondues** : « dracaufeu » et "
        "« charizard » renvoient les mêmes cartes. Insensible à la casse et aux "
        "accents, tolérante aux fautes de frappe (trigram).\n\n"
        "Sans `q`, parcourt le référentiel selon les filtres — de quoi afficher "
        "une extension entière.\n\n"
        "Les filtres portent sur les **codes**, jamais sur les libellés "
        "traduits : `rarity=uncommon`, pas `rarity=Peu Commune`."
    ),
)
async def search_cards(
    q: str | None = Query(
        None,
        description="Terme recherché, dans n'importe quelle langue importée.",
        examples=["dracaufeu", "charizard"],
    ),
    tcg: str | None = Query(None, description="Code du TCG, ex. `pokemon`."),
    expansion: str | None = Query(None, description="Code d'extension, ex. `base1`."),
    rarity: str | None = Query(None, description="Code de rareté, ex. `uncommon`."),
    card_type: str | None = Query(None, description="Code de type, ex. `pokemon`."),
    language: str | None = Query(
        None,
        description="Restreint la recherche à une langue. Par défaut, toutes.",
        examples=["fr"],
    ),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    session: AsyncSession = Depends(get_session),
) -> Page[CardSummary]:
    terme = (q or "").strip()
    if terme and len(terme) < MIN_QUERY_LENGTH:
        # 422 en clair plutôt que la constante Starlette, qui a été renommée
        # entre deux versions : le code numérique, lui, ne bougera pas.
        raise HTTPException(
            422,
            detail=f"Le terme doit faire au moins {MIN_QUERY_LENGTH} caractères.",
        )

    cartes, scores, total = await search_service.search_cards(
        session,
        query=terme or None,
        filtres=CardFilters(
            tcg=tcg,
            expansion=expansion,
            rarity=rarity,
            card_type=card_type,
            language=language,
        ),
        page=page,
        page_size=page_size,
    )
    return Page[CardSummary](
        items=[CardSummary.from_card(c, scores.get(c.id)) for c in cartes],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get(
    "/{card_id}",
    response_model=CardDetail,
    summary="Détail d'une carte",
    description=(
        "Renvoie la carte avec **toutes ses impressions** — c'est à ce niveau "
        "que se rattacheront les prix (lot 5) et les exemplaires possédés "
        "(lot 4) — et toutes ses localisations."
    ),
    responses={404: {"description": "Aucune carte avec cet identifiant."}},
)
async def get_card(
    card_id: int,
    session: AsyncSession = Depends(get_session),
) -> CardDetail:
    carte = await search_service.get_card(session, card_id)
    if carte is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Carte introuvable.")
    return CardDetail.from_card(carte)

"""Gestion de la collection : possession, ventes, statistiques."""

from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_session
from app.models.enums import CardCondition
from app.schemas.collection import (
    CollectionStats,
    CompletionOut,
    HoldingOut,
    ItemCreate,
    ItemUpdate,
    LotOut,
    SaleCreate,
    SaleOut,
    ValuationOut,
    ValuationPoint,
    build_printing_ref,
)
from app.schemas.common import Page
from app.services import collection as service
from app.services import stats as stats_service
from app.services import valuation as valuation_service
from app.services.collection import CollectionError, HoldingFilters

router = APIRouter(prefix="/collection", tags=["collection"])


def _conflit(exc: CollectionError) -> HTTPException:
    """409 et non 400 : la demande est bien formée, c'est l'état de la
    collection qui la rend impossible."""
    return HTTPException(status.HTTP_409_CONFLICT, detail=str(exc))


@router.get(
    "/items",
    response_model=Page[HoldingOut],
    summary="Lister la collection",
    description=(
        "Groupé **par impression** : une entrée par carte précise, dans une "
        "finition et une langue précises. Dix Pikachu dont cinq identiques "
        "donnent une entrée à 5 et plusieurs entrées à 1 — jamais une entrée "
        "« Pikachu ×10 ».\n\n"
        "Chaque entrée porte l'URL de l'image de la langue possédée, pour que "
        "le front affiche une miniature."
    ),
)
async def list_items(
    q: str | None = Query(None, description="Recherche par nom, toutes langues."),
    tcg: str | None = None,
    expansion: str | None = None,
    rarity: str | None = None,
    finish: str | None = None,
    language: str | None = None,
    condition: CardCondition | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    session: AsyncSession = Depends(get_session),
) -> Page[HoldingOut]:
    entrees, total = await service.list_holdings(
        session,
        filtres=HoldingFilters(
            q=q.strip() if q else None,
            tcg=tcg,
            expansion=expansion,
            rarity=rarity,
            finish=finish,
            language=language,
            condition=condition,
        ),
        page=page,
        page_size=page_size,
    )

    items = []
    for entree in entrees:
        lots = [LotOut.model_validate(lot) for lot in entree["lots"]]
        par_etat: dict[str, int] = {}
        for lot in lots:
            par_etat[lot.condition.value] = par_etat.get(lot.condition.value, 0) + lot.quantity
        items.append(
            HoldingOut(
                printing=build_printing_ref(entree["printing"]),
                quantity=entree["quantity"],
                by_condition=par_etat,
                lots=lots,
            )
        )
    return Page[HoldingOut](items=items, total=total, page=page, page_size=page_size)


@router.post(
    "/items",
    response_model=LotOut,
    status_code=status.HTTP_201_CREATED,
    summary="Ajouter des exemplaires",
    description=(
        "**Sans prix d'achat**, l'ajout fusionne avec une ligne existante de "
        "même impression et même état : c'est le geste courant, celui qui suit "
        "l'ouverture d'un booster. La réponse est alors `200`.\n\n"
        "**Avec un prix d'achat**, un lot distinct est créé (`201`) : ce prix "
        "de revient lui est propre, le fusionner le perdrait."
    ),
    responses={409: {"description": "Impression inexistante ou données incohérentes."}},
)
async def add_item(
    payload: ItemCreate,
    response: Response,
    session: AsyncSession = Depends(get_session),
) -> LotOut:
    try:
        ligne, cree = await service.add_item(
            session,
            printing_id=payload.printing_id,
            quantity=payload.quantity,
            condition=payload.condition,
            unit_purchase_price=payload.unit_purchase_price,
            purchase_currency=payload.purchase_currency,
            purchase_date=payload.purchase_date,
            notes=payload.notes,
        )
    except CollectionError as exc:
        raise _conflit(exc) from exc
    await session.commit()
    if not cree:
        response.status_code = status.HTTP_200_OK
    return LotOut.model_validate(ligne)


@router.patch(
    "/items/{item_id}",
    response_model=LotOut,
    summary="Modifier un lot",
    responses={404: {"description": "Lot introuvable."}},
)
async def update_item(
    item_id: int,
    payload: ItemUpdate,
    session: AsyncSession = Depends(get_session),
) -> LotOut:
    changements = payload.model_dump(exclude_unset=True)
    if not changements:
        raise HTTPException(422, detail="Aucun champ à modifier.")
    try:
        ligne = await service.update_item(session, item_id, changements)
    except CollectionError as exc:
        raise _conflit(exc) from exc
    await session.commit()
    return LotOut.model_validate(ligne)


@router.delete(
    "/items/{item_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Retirer un lot",
    description=(
        "Retire purement la ligne. **Si la carte a été vendue, enregistrer une "
        "vente plutôt qu'une suppression** : la suppression ne laisse aucune "
        "trace et ne compte pas dans le total des ventes."
    ),
)
async def delete_item(
    item_id: int, session: AsyncSession = Depends(get_session)
) -> Response:
    try:
        await service.delete_item(session, item_id)
    except CollectionError as exc:
        raise _conflit(exc) from exc
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/sales",
    response_model=SaleOut,
    status_code=status.HTTP_201_CREATED,
    summary="Enregistrer une vente",
    description=(
        "Retire les exemplaires de la collection **et** crée une ligne de "
        "vente. Sans `collection_item_id`, les exemplaires sont pris sur les "
        "lots les plus anciens d'abord.\n\n"
        "Refuse en `409` si la collection ne contient pas assez d'exemplaires."
    ),
)
async def create_sale(
    payload: SaleCreate, session: AsyncSession = Depends(get_session)
) -> SaleOut:
    try:
        vente = await service.record_sale(
            session,
            printing_id=payload.printing_id,
            quantity=payload.quantity,
            unit_sale_price=payload.unit_sale_price,
            sale_currency=payload.sale_currency,
            sale_date=payload.sale_date,
            platform=payload.platform,
            condition=payload.condition,
            collection_item_id=payload.collection_item_id,
            notes=payload.notes,
        )
    except CollectionError as exc:
        raise _conflit(exc) from exc
    await session.commit()
    # Relecture après commit : le commit expire les objets, et rendre la vente
    # sans recharger ses relations lèverait un MissingGreenlet.
    rechargee = await service.get_sale(session, vente.id)
    assert rechargee is not None
    return _sale_out(rechargee)


@router.get("/sales", response_model=Page[SaleOut], summary="Historique des ventes")
async def list_sales(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    session: AsyncSession = Depends(get_session),
) -> Page[SaleOut]:
    ventes, total = await service.list_sales(session, page=page, page_size=page_size)
    return Page[SaleOut](
        items=[_sale_out(v) for v in ventes], total=total, page=page, page_size=page_size
    )


@router.delete(
    "/sales/{sale_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Annuler une vente",
    description=(
        "**Ne remet rien en collection** : on ne sait pas dans quel lot "
        "remettre l'exemplaire. Réajouter la carte est un geste distinct."
    ),
)
async def delete_sale(
    sale_id: int, session: AsyncSession = Depends(get_session)
) -> Response:
    try:
        await service.delete_sale(session, sale_id)
    except CollectionError as exc:
        raise _conflit(exc) from exc
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get(
    "/stats",
    response_model=CollectionStats,
    summary="Statistiques de la collection",
    description=(
        "Trois compteurs, parce que « combien de cartes ai-je ? » a trois "
        "réponses : `items` (cartes physiques), `distinct_printings` (versions "
        "différentes), `distinct_cards` (cartes du référentiel).\n\n"
        "`purchase_value` est ce que tu as **payé**, `market_value` ce que la "
        "collection **vaut** à la dernière cote connue. Les deux sont rendus "
        "par devise, sans aucune conversion."
    ),
)
async def get_stats(
    tcg: str | None = None, session: AsyncSession = Depends(get_session)
) -> CollectionStats:
    donnees = await stats_service.collection_stats(session, tcg=tcg)
    # La valeur de marché vit dans son propre service : les statistiques la
    # rapatrient pour qu'un tableau de bord tienne en un seul appel.
    marche = await valuation_service.valuation(session, tcg=tcg)
    donnees["market_value"] = marche["totals"]
    donnees["printings_without_quote"] = marche["uncovered_printings"]
    return CollectionStats(**donnees)


@router.get(
    "/completion",
    response_model=list[CompletionOut],
    summary="Complétion par extension",
    description=(
        "Une carte compte dès qu'on en possède une impression, **quelle que "
        "soit sa langue**. `language` restreint à une langue pour qui vise un "
        "set dans une seule langue.\n\n"
        "Deux dénominateurs : `official` est le total imprimé sur la carte, "
        "`total` inclut les cartes secrètes."
    ),
)
async def get_completion(
    tcg: str | None = None,
    language: str | None = None,
    include_empty: bool = Query(
        False, description="Inclure les extensions dont on ne possède rien."
    ),
    session: AsyncSession = Depends(get_session),
) -> list[CompletionOut]:
    lignes = await stats_service.completion(
        session, tcg=tcg, language=language, include_empty=include_empty
    )
    return [CompletionOut(**ligne) for ligne in lignes]


def _sale_out(vente: object) -> SaleOut:
    return SaleOut(
        id=vente.id,
        quantity=vente.quantity,
        condition=vente.condition,
        unit_sale_price=vente.unit_sale_price,
        sale_currency=vente.sale_currency,
        sale_date=vente.sale_date,
        platform=vente.platform.label if vente.platform else None,
        notes=vente.notes,
        printing=build_printing_ref(vente.printing),
    )


@router.get(
    "/valuation",
    response_model=ValuationOut,
    summary="Valeur de marché de la collection",
    description=(
        "Applique à chaque impression possédée sa **dernière cote connue**, "
        "multipliée par la quantité. C'est ce qui donne une valeur aux cartes "
        "dont tu n'as saisi aucun prix d'achat.\n\n"
        "La cote NM s'applique à tous les états. Les montants sont rendus par "
        "devise, sans conversion. Les impressions sans cote sont comptées à "
        "part : elles ne valent pas zéro, leur valeur est inconnue.\n\n"
        "`oldest_quote` dit sur quelles dates s'appuie le total — à toi de "
        "juger si c'est frais."
    ),
)
async def get_valuation(
    at: date | None = Query(None, description="Valeur à cette date. Défaut : aujourd'hui."),
    tcg: str | None = None,
    source: str | None = Query(None, description="Code de source, ex. `cardmarket`."),
    session: AsyncSession = Depends(get_session),
) -> ValuationOut:
    return ValuationOut(
        **await valuation_service.valuation(session, as_of=at, tcg=tcg, source=source)
    )


@router.get(
    "/valuation/history",
    response_model=list[ValuationPoint],
    summary="Évolution de la valeur",
    description=(
        "Série temporelle sur 7, 30 ou 365 jours.\n\n"
        "⚠️ Les cotes passées sont appliquées à la collection **d'aujourd'hui**. "
        "La courbe répond donc à « combien vaudrait ma collection actuelle aux "
        "prix d'alors », pas à « combien valait ma collection alors » — "
        "reconstituer la seconde exigerait un historique des possessions que le "
        "schéma ne garde pas."
    ),
)
async def get_valuation_history(
    days: int = Query(30, ge=1, le=1825, description="Profondeur en jours."),
    step_days: int = Query(1, ge=1, le=30, description="Pas d'échantillonnage."),
    tcg: str | None = None,
    source: str | None = None,
    session: AsyncSession = Depends(get_session),
) -> list[ValuationPoint]:
    points = await valuation_service.valuation_history(
        session, days=days, tcg=tcg, source=source, step_days=step_days
    )
    return [ValuationPoint(**p) for p in points]

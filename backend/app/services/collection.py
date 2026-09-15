"""Gestion de la collection : possession, ventes, statistiques.

Quatre règles portent ce module.

**1. On ne regroupe jamais par nom.** « Dracaufeu » désigne des dizaines de
cartes différentes. La clé de regroupement est l'**impression** — donc une
carte précise, d'une extension précise, dans une finition et une langue
précises. Le nom n'intervient nulle part comme identifiant.

**2. Un ajout simple incrémente, un ajout avec prix crée un lot.** Ajouter une
carte sortie d'un booster est le geste courant : il augmente la quantité d'une
ligne existante. Mais une carte achetée 180 € a son propre prix de revient :
fusionner la ligne le perdrait sans retour.

**3. Une vente sort de la collection et entre dans un registre séparé.**
Vendre le dernier exemplaire supprime la ligne de collection ; la vente, elle,
doit survivre. D'où une table dédiée et un lien nullable.

**4. On ne convertit jamais une devise.** Les totaux sont rendus groupés par
devise. Additionner des euros et des dollars avec un taux inventé produirait
un chiffre faux et crédible — le pire des deux mondes.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from sqlalchemy import Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models import (
    Card,
    CardLocalization,
    CollectionItem,
    CollectionSale,
    Expansion,
    Finish,
    Printing,
    Rarity,
    SalePlatform,
    Tcg,
)
from app.models.enums import CardCondition
from app.services.search import escape_like, normalize_term


class CollectionError(Exception):
    """Erreur métier : la demande est cohérente mais impossible en l'état."""


@dataclass(frozen=True)
class HoldingFilters:
    """Filtres du listing de collection."""

    q: str | None = None
    tcg: str | None = None
    expansion: str | None = None
    rarity: str | None = None
    finish: str | None = None
    language: str | None = None
    condition: CardCondition | None = None


# ---------------------------------------------------------------------------
# Écriture : ajout, modification, retrait
# ---------------------------------------------------------------------------


async def add_item(
    session: AsyncSession,
    *,
    printing_id: int,
    quantity: int = 1,
    condition: CardCondition = CardCondition.NEAR_MINT,
    unit_purchase_price: Decimal | None = None,
    purchase_currency: str | None = None,
    purchase_date: Any = None,
    notes: str | None = None,
) -> tuple[CollectionItem, bool]:
    """Ajoute des exemplaires. Renvoie la ligne et si elle a été créée.

    **Sans prix d'achat**, on fusionne avec une ligne existante de même
    impression, même état et **sans prix non plus** : c'est l'ajout courant,
    celui qui suit l'ouverture d'un booster.

    **Avec un prix d'achat**, on crée toujours une ligne : elle porte un prix
    de revient qui lui est propre, et le fusionner le détruirait.
    """
    if quantity < 1:
        raise CollectionError("La quantité doit être d'au moins 1.")
    if (unit_purchase_price is None) != (purchase_currency is None):
        raise CollectionError(
            "Le prix d'achat et sa devise vont ensemble : fournir les deux ou aucun."
        )

    if await session.get(Printing, printing_id) is None:
        raise CollectionError(f"L'impression {printing_id} n'existe pas.")

    if unit_purchase_price is None:
        existante = (
            await session.execute(
                select(CollectionItem)
                .where(
                    CollectionItem.printing_id == printing_id,
                    CollectionItem.condition == condition,
                    CollectionItem.unit_purchase_price.is_(None),
                )
                .order_by(CollectionItem.id)
                .limit(1)
            )
        ).scalar_one_or_none()
        if existante is not None:
            existante.quantity += quantity
            if notes:
                existante.notes = notes
            await session.flush()
            return existante, False

    ligne = CollectionItem(
        printing_id=printing_id,
        quantity=quantity,
        condition=condition,
        unit_purchase_price=unit_purchase_price,
        purchase_currency=purchase_currency,
        purchase_date=purchase_date,
        notes=notes,
    )
    session.add(ligne)
    await session.flush()
    return ligne, True


async def update_item(
    session: AsyncSession, item_id: int, changements: dict[str, Any]
) -> CollectionItem:
    """Modifie une ligne. Une quantité nulle n'est pas un retrait déguisé."""
    ligne = await session.get(CollectionItem, item_id)
    if ligne is None:
        raise CollectionError(f"La ligne {item_id} n'existe pas.")

    for champ, valeur in changements.items():
        setattr(ligne, champ, valeur)

    if ligne.quantity < 1:
        raise CollectionError(
            "Une quantité nulle ne retire pas la ligne : utiliser DELETE, "
            "ou enregistrer une vente si la carte a été vendue."
        )
    if (ligne.unit_purchase_price is None) != (ligne.purchase_currency is None):
        raise CollectionError(
            "Le prix d'achat et sa devise vont ensemble : fournir les deux ou aucun."
        )
    await session.flush()
    return ligne


async def delete_item(session: AsyncSession, item_id: int) -> None:
    ligne = await session.get(CollectionItem, item_id)
    if ligne is None:
        raise CollectionError(f"La ligne {item_id} n'existe pas.")
    await session.delete(ligne)
    await session.flush()


# ---------------------------------------------------------------------------
# Ventes
# ---------------------------------------------------------------------------


async def _platform_id(session: AsyncSession, nom: str | None) -> int | None:
    """Résout ou crée une plateforme de vente.

    Le code est normalisé pour que « Vinted », « vinted » et « VINTED » ne
    deviennent pas trois plateformes distinctes dans les statistiques ; le
    libellé garde la saisie d'origine.
    """
    if not nom or not nom.strip():
        return None
    from app.importers.tcgdex.mapping import normalize_code

    code = normalize_code(nom)
    if not code:
        return None
    existante = (
        await session.execute(select(SalePlatform).where(SalePlatform.code == code))
    ).scalar_one_or_none()
    if existante is not None:
        return existante.id
    plateforme = SalePlatform(code=code, label=nom.strip())
    session.add(plateforme)
    await session.flush()
    return plateforme.id


async def record_sale(
    session: AsyncSession,
    *,
    printing_id: int,
    quantity: int,
    unit_sale_price: Decimal,
    sale_currency: str = "EUR",
    sale_date: Any = None,
    platform: str | None = None,
    condition: CardCondition | None = None,
    collection_item_id: int | None = None,
    notes: str | None = None,
) -> CollectionSale:
    """Enregistre une vente et retire les exemplaires de la collection.

    Sans `collection_item_id`, les exemplaires sont pris sur les lots les plus
    anciens d'abord — un ordre arbitraire mais **stable**, préférable à un
    choix dépendant de l'ordre de lecture de la base.

    La vente est **une seule ligne**, même si elle a puisé dans plusieurs lots.
    `collection_item_id` n'est renseigné que lorsqu'un seul lot a été touché
    **et qu'il survit** : prétendre rattacher une vente étalée à un lot unique
    serait faux, et pointer un lot vidé — donc supprimé dans la même
    transaction — violerait la clé étrangère.
    """
    if quantity < 1:
        raise CollectionError("La quantité vendue doit être d'au moins 1.")
    if unit_sale_price < 0:
        raise CollectionError("Le prix de vente ne peut pas être négatif.")

    if collection_item_id is not None:
        ligne = await session.get(CollectionItem, collection_item_id)
        if ligne is None or ligne.printing_id != printing_id:
            raise CollectionError(
                f"La ligne {collection_item_id} ne correspond pas à l'impression "
                f"{printing_id}."
            )
        lots = [ligne]
    else:
        lots = list(
            (
                await session.execute(
                    select(CollectionItem)
                    .where(CollectionItem.printing_id == printing_id)
                    .order_by(
                        CollectionItem.purchase_date.nullsfirst(), CollectionItem.id
                    )
                )
            ).scalars()
        )

    disponible = sum(lot.quantity for lot in lots)
    if disponible < quantity:
        raise CollectionError(
            f"Vente impossible : {quantity} demandé(s), {disponible} en collection."
        )

    # L'état vendu par défaut est celui du premier lot touché : c'est le plus
    # probable, et il reste modifiable.
    if condition is None:
        condition = lots[0].condition if lots else CardCondition.NEAR_MINT

    restant = quantity
    lots_touches: list[int] = []
    lots_survivants: list[int] = []
    for lot in lots:
        if restant == 0:
            break
        pris = min(lot.quantity, restant)
        lot.quantity -= pris
        restant -= pris
        lots_touches.append(lot.id)
        if lot.quantity == 0:
            await session.delete(lot)
        else:
            lots_survivants.append(lot.id)

    vente = CollectionSale(
        printing_id=printing_id,
        collection_item_id=(
            lots_survivants[0]
            if len(lots_touches) == 1 and lots_survivants
            else None
        ),
        quantity=quantity,
        condition=condition,
        unit_sale_price=unit_sale_price,
        sale_currency=sale_currency,
        sale_date=sale_date,
        platform_id=await _platform_id(session, platform),
        notes=notes,
    )
    session.add(vente)
    await session.flush()
    return vente


async def delete_sale(session: AsyncSession, sale_id: int) -> None:
    """Annule une vente. **Ne remet rien en collection** : le faire supposerait
    de savoir dans quel lot remettre l'exemplaire, ce qu'on ne sait pas."""
    vente = await session.get(CollectionSale, sale_id)
    if vente is None:
        raise CollectionError(f"La vente {sale_id} n'existe pas.")
    await session.delete(vente)
    await session.flush()


# ---------------------------------------------------------------------------
# Lecture
# ---------------------------------------------------------------------------


def _join_catalog(stmt: Select[Any], filtres: HoldingFilters) -> Select[Any]:
    """Joint l'arbre du référentiel et applique les filtres.

    Les filtres portent sur les **codes**, jamais sur les libellés traduits :
    même règle qu'au lot 3.
    """
    stmt = stmt.join(Printing, Printing.id == CollectionItem.printing_id).join(
        Card, Card.id == Printing.card_id
    )
    # Expansion est toujours jointe : le tri par défaut s'appuie dessus.
    stmt = stmt.join(Expansion, Expansion.id == Card.expansion_id)
    if filtres.expansion:
        stmt = stmt.where(Expansion.code == filtres.expansion)
    if filtres.tcg:
        stmt = stmt.join(Tcg, Tcg.id == Expansion.tcg_id).where(Tcg.code == filtres.tcg)
    if filtres.rarity:
        stmt = stmt.join(Rarity, Rarity.id == Card.rarity_id).where(
            Rarity.code == filtres.rarity
        )
    if filtres.finish:
        stmt = stmt.join(Finish, Finish.id == Printing.finish_id).where(
            Finish.code == filtres.finish
        )
    if filtres.language:
        stmt = stmt.where(Printing.language == filtres.language)
    if filtres.condition:
        stmt = stmt.where(CollectionItem.condition == filtres.condition)
    return stmt


async def _apply_text_search(
    session: AsyncSession, stmt: Select[Any], terme: str
) -> Select[Any]:
    """Restreint aux cartes dont un nom, dans n'importe quelle langue,
    correspond au terme. Même mécanique qu'au lot 3, index compris."""
    normalise = await normalize_term(session, terme)
    motif = f"%{escape_like(normalise)}%"
    sous_requete = select(CardLocalization.card_id).where(
        CardLocalization.name_normalized.op("%")(normalise)
        | CardLocalization.name_normalized.like(motif, escape="\\")
    )
    return stmt.where(Card.id.in_(sous_requete))


async def list_holdings(
    session: AsyncSession,
    *,
    filtres: HoldingFilters,
    page: int,
    page_size: int,
) -> tuple[list[dict[str, Any]], int]:
    """Liste la collection **groupée par impression**.

    Une entrée = une carte précise, dans une finition et une langue précises.
    Dix Pikachu dont cinq identiques donnent donc une entrée à 5 et plusieurs
    entrées à 1 — jamais une entrée « Pikachu ×10 ».
    """
    base = select(
        CollectionItem.printing_id,
        func.sum(CollectionItem.quantity).label("quantity"),
    ).group_by(CollectionItem.printing_id)
    base = _join_catalog(base, filtres)
    if filtres.q:
        base = await _apply_text_search(session, base, filtres.q)

    comptage = select(func.count()).select_from(base.subquery())
    total = (await session.execute(comptage)).scalar_one()

    # Tri : extension la plus récente d'abord, puis numéro de carte. C'est
    # l'ordre dans lequel on parcourt un classeur, pas un ordre technique.
    #
    # Les colonnes de tri passent par `max()` plutôt que d'entrer dans le
    # GROUP BY : une impression n'appartient qu'à une carte et une extension,
    # donc l'agrégat vaut la valeur elle-même — mais PostgreSQL ne peut pas
    # le déduire à travers les jointures et refuserait la requête.
    stmt = (
        base.order_by(
            func.max(Expansion.release_date).desc().nullslast(),
            func.max(Card.number_sort).nullslast(),
            CollectionItem.printing_id,
        )
        .limit(page_size)
        .offset((page - 1) * page_size)
    )
    lignes = (await session.execute(stmt)).all()

    if not lignes:
        return [], total

    printing_ids = [ligne.printing_id for ligne in lignes]
    quantites = {ligne.printing_id: int(ligne.quantity) for ligne in lignes}

    impressions = await _load_printings(session, printing_ids)
    lots = await _load_lots(session, printing_ids)

    entrees = []
    for pid in printing_ids:
        impression = impressions.get(pid)
        if impression is None:
            continue
        entrees.append(
            {
                "printing": impression,
                "quantity": quantites[pid],
                "lots": lots.get(pid, []),
            }
        )
    return entrees, total


async def _load_printings(
    session: AsyncSession, printing_ids: list[int]
) -> dict[int, Printing]:
    """Charge les impressions avec tout leur contexte, sans requête par ligne."""
    stmt = (
        select(Printing)
        .where(Printing.id.in_(printing_ids))
        .options(
            selectinload(Printing.finish),
            selectinload(Printing.card).selectinload(Card.localizations),
            selectinload(Printing.card).selectinload(Card.rarity),
            selectinload(Printing.card)
            .selectinload(Card.expansion)
            .selectinload(Expansion.names),
        )
    )
    return {p.id: p for p in (await session.execute(stmt)).scalars().unique()}


async def _load_lots(
    session: AsyncSession, printing_ids: list[int]
) -> dict[int, list[CollectionItem]]:
    stmt = (
        select(CollectionItem)
        .where(CollectionItem.printing_id.in_(printing_ids))
        .order_by(CollectionItem.purchase_date.nullsfirst(), CollectionItem.id)
    )
    groupes: dict[int, list[CollectionItem]] = {}
    for ligne in (await session.execute(stmt)).scalars():
        groupes.setdefault(ligne.printing_id, []).append(ligne)
    return groupes


def _sale_options() -> list[Any]:
    """Relations à précharger pour rendre une vente.

    Définies une seule fois : en async, une relation oubliée ne donne pas une
    requête de plus mais un `MissingGreenlet` en pleine réponse HTTP.
    """
    return [
        selectinload(CollectionSale.platform),
        selectinload(CollectionSale.printing).selectinload(Printing.finish),
        selectinload(CollectionSale.printing)
        .selectinload(Printing.card)
        .selectinload(Card.localizations),
        selectinload(CollectionSale.printing)
        .selectinload(Printing.card)
        .selectinload(Card.rarity),
        selectinload(CollectionSale.printing)
        .selectinload(Printing.card)
        .selectinload(Card.expansion)
        .selectinload(Expansion.names),
    ]


async def get_sale(session: AsyncSession, sale_id: int) -> CollectionSale | None:
    """Relit une vente avec tout son contexte, après commit."""
    stmt = select(CollectionSale).where(CollectionSale.id == sale_id).options(*_sale_options())
    return (await session.execute(stmt)).scalars().unique().one_or_none()


async def list_sales(
    session: AsyncSession, *, page: int, page_size: int
) -> tuple[list[CollectionSale], int]:
    total = (
        await session.execute(select(func.count(CollectionSale.id)))
    ).scalar_one()
    stmt = (
        select(CollectionSale)
        .options(*_sale_options())
        .order_by(CollectionSale.sale_date.desc().nullslast(), CollectionSale.id.desc())
        .limit(page_size)
        .offset((page - 1) * page_size)
    )
    return list((await session.execute(stmt)).scalars().unique()), total

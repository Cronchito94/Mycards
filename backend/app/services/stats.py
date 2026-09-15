"""Statistiques de collection et complétion par extension.

Deux principes valent d'être connus avant de lire les requêtes.

**Aucune conversion de devise.** Chaque total est rendu *par devise*.
Additionner des euros et des dollars avec un taux inventé donnerait un chiffre
faux et crédible, ce qui est pire qu'une absence de chiffre.

**Trois compteurs, pas un.** « Combien de cartes ai-je ? » n'a pas une réponse
mais trois, et l'écart est énorme sur un référentiel où 14 044 cartes ont deux
impressions et 8 356 en ont quatre :

- **exemplaires** — les cartes physiques, doublons compris ;
- **impressions distinctes** — combien de versions différentes (une normale et
  sa reverse comptent pour deux) ;
- **cartes distinctes** — combien de cartes du référentiel (la même normale et
  sa reverse comptent pour une).
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    Card,
    CollectionItem,
    CollectionSale,
    Expansion,
    ExpansionName,
    Printing,
    SalePlatform,
    Tcg,
)


async def collection_stats(
    session: AsyncSession, *, tcg: str | None = None
) -> dict[str, Any]:
    """Tableau de bord de la collection."""
    base = (
        select(CollectionItem)
        .join(Printing, Printing.id == CollectionItem.printing_id)
        .join(Card, Card.id == Printing.card_id)
        .join(Expansion, Expansion.id == Card.expansion_id)
    )
    if tcg:
        base = base.join(Tcg, Tcg.id == Expansion.tcg_id).where(Tcg.code == tcg)
    filtre = base.whereclause

    def compte(colonne: Any) -> Any:
        stmt = (
            select(colonne)
            .select_from(CollectionItem)
            .join(Printing, Printing.id == CollectionItem.printing_id)
            .join(Card, Card.id == Printing.card_id)
            .join(Expansion, Expansion.id == Card.expansion_id)
        )
        if tcg:
            stmt = stmt.join(Tcg, Tcg.id == Expansion.tcg_id)
        return stmt.where(filtre) if filtre is not None else stmt

    exemplaires = (
        await session.execute(compte(func.coalesce(func.sum(CollectionItem.quantity), 0)))
    ).scalar_one()
    impressions = (
        await session.execute(compte(func.count(func.distinct(Printing.id))))
    ).scalar_one()
    cartes = (
        await session.execute(compte(func.count(func.distinct(Card.id))))
    ).scalar_one()

    par_etat = (
        await session.execute(
            compte(CollectionItem.condition)
            .add_columns(func.sum(CollectionItem.quantity))
            .group_by(CollectionItem.condition)
        )
    ).all()

    # Valeur d'achat : seules les lignes qui portent un prix comptent. Les
    # autres ne valent pas zéro — leur prix est simplement inconnu, et c'est
    # une information différente qu'on rend explicite.
    achats = (
        await session.execute(
            compte(CollectionItem.purchase_currency)
            .add_columns(
                func.sum(
                    CollectionItem.unit_purchase_price * CollectionItem.quantity
                ),
                func.sum(CollectionItem.quantity),
            )
            .where(CollectionItem.unit_purchase_price.is_not(None))
            .group_by(CollectionItem.purchase_currency)
        )
    ).all()
    sans_prix = (
        await session.execute(
            compte(func.coalesce(func.sum(CollectionItem.quantity), 0)).where(
                CollectionItem.unit_purchase_price.is_(None)
            )
        )
    ).scalar_one()

    ventes = (
        await session.execute(
            select(
                CollectionSale.sale_currency,
                func.sum(CollectionSale.unit_sale_price * CollectionSale.quantity),
                func.sum(CollectionSale.quantity),
            ).group_by(CollectionSale.sale_currency)
        )
    ).all()

    par_plateforme = (
        await session.execute(
            select(
                func.coalesce(SalePlatform.label, "(non renseignée)"),
                CollectionSale.sale_currency,
                func.sum(CollectionSale.unit_sale_price * CollectionSale.quantity),
                func.sum(CollectionSale.quantity),
            )
            .select_from(CollectionSale)
            .join(SalePlatform, SalePlatform.id == CollectionSale.platform_id, isouter=True)
            .group_by(SalePlatform.label, CollectionSale.sale_currency)
            .order_by(func.sum(CollectionSale.unit_sale_price * CollectionSale.quantity).desc())
        )
    ).all()

    return {
        "items": int(exemplaires),
        "distinct_printings": int(impressions),
        "distinct_cards": int(cartes),
        "by_condition": {
            etat.value: int(nb) for etat, nb in par_etat if etat is not None
        },
        "purchase_value": [
            {"currency": devise, "total": montant, "quantity": int(nb)}
            for devise, montant, nb in achats
        ],
        "items_without_price": int(sans_prix),
        "sales_total": [
            {"currency": devise, "total": montant, "quantity": int(nb)}
            for devise, montant, nb in ventes
        ],
        "sales_by_platform": [
            {
                "platform": plateforme,
                "currency": devise,
                "total": montant,
                "quantity": int(nb),
            }
            for plateforme, devise, montant, nb in par_plateforme
        ],
    }


async def completion(
    session: AsyncSession,
    *,
    tcg: str | None = None,
    language: str | None = None,
    include_empty: bool = False,
) -> list[dict[str, Any]]:
    """Complétion par extension.

    **Une carte compte dès qu'on en possède une impression, quelle que soit sa
    langue** — décision du 15/09/2026 : une Charizard anglaise complète le set
    au même titre que sa version française. Le paramètre `language` permet de
    restreindre à une langue pour qui vise un set dans une seule langue.

    Deux dénominateurs sont rendus, parce qu'ils racontent deux choses :
    `official` est le total imprimé sur la carte (le 102 de « 4/102 »), `total`
    inclut les cartes secrètes. `165/165` et `165/207` ne veulent pas dire la
    même chose, et le choix revient à celui qui collectionne.
    """
    possedees = (
        select(
            Card.expansion_id.label("expansion_id"),
            func.count(func.distinct(Card.id)).label("owned"),
        )
        .select_from(CollectionItem)
        .join(Printing, Printing.id == CollectionItem.printing_id)
        .join(Card, Card.id == Printing.card_id)
        .group_by(Card.expansion_id)
    )
    if language:
        possedees = possedees.where(Printing.language == language)
    possedees = possedees.subquery()

    # `card_count_total` vient de la source et peut manquer ; on retombe sur le
    # nombre de cartes réellement en base, qui est vrai par construction.
    en_base = (
        select(
            Card.expansion_id.label("expansion_id"),
            func.count(Card.id).label("in_db"),
        )
        .group_by(Card.expansion_id)
        .subquery()
    )

    stmt = (
        select(
            Expansion.id,
            Expansion.code,
            Expansion.release_date,
            Expansion.card_count_official,
            Expansion.card_count_total,
            func.coalesce(possedees.c.owned, 0).label("owned"),
            en_base.c.in_db,
        )
        .join(en_base, en_base.c.expansion_id == Expansion.id)
        .join(possedees, possedees.c.expansion_id == Expansion.id, isouter=True)
        .order_by(Expansion.release_date.desc().nullslast(), Expansion.code)
    )
    if tcg:
        stmt = stmt.join(Tcg, Tcg.id == Expansion.tcg_id).where(Tcg.code == tcg)
    if not include_empty:
        # Sans ce filtre, on renverrait 221 extensions à zéro : illisible.
        stmt = stmt.where(possedees.c.owned.is_not(None))

    lignes = (await session.execute(stmt)).all()
    if not lignes:
        return []

    noms = await _expansion_names(session, [ligne.id for ligne in lignes])

    resultats = []
    for ligne in lignes:
        total = ligne.card_count_total or ligne.in_db
        officiel = ligne.card_count_official
        resultats.append(
            {
                "expansion": {
                    "id": ligne.id,
                    "code": ligne.code,
                    "release_date": ligne.release_date,
                    "names": noms.get(ligne.id, {}),
                },
                "owned": int(ligne.owned),
                "official": officiel,
                "total": total,
                "percent_official": (
                    round(100 * ligne.owned / officiel, 1) if officiel else None
                ),
                "percent_total": round(100 * ligne.owned / total, 1) if total else None,
            }
        )
    return resultats


async def _expansion_names(
    session: AsyncSession, expansion_ids: list[int]
) -> dict[int, dict[str, str]]:
    stmt = select(ExpansionName).where(ExpansionName.expansion_id.in_(expansion_ids))
    noms: dict[int, dict[str, str]] = {}
    for n in (await session.execute(stmt)).scalars():
        noms.setdefault(n.expansion_id, {})[n.language] = n.name
    return noms

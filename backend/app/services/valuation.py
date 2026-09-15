"""Valorisation de la collection au prix du marché.

C'est la réponse à « une carte sans prix saisi ne vaut pas zéro » : faute de
prix d'achat, on applique la **dernière cote connue** de l'impression.

Quatre règles, arbitrées avec l'utilisateur le 15/09/2026.

**1. La cote NM s'applique à tous les états.** Sorti de booster, une carte est
NM ; les autres états ne viennent que d'achats. C'est le choix de Collectr.
Contrepartie assumée : sur une carte vintage, une GD vaut souvent 20 à 30 %
d'une NM, donc la valeur est *surévaluée* sur ces cartes-là.

**2. L'euro fait référence**, via Cardmarket. Les montants restent rendus par
devise : aucune conversion, jamais.

**3. La cote la plus récente**, sans limite d'ancienneté imposée. On rend la
date du relevé le plus vieux utilisé — c'est au lecteur de juger si c'est
frais, pas au serveur de décider en silence.

**4. Les impressions sans cote sont comptées à part.** Elles ne valent pas
zéro : leur valeur est inconnue. Les noyer dans le total donnerait un chiffre
faux et crédible.

⚠️ **Ce que la série temporelle dit — et ne dit pas.** Elle applique les cotes
passées à la collection **d'aujourd'hui**. Elle répond donc à « combien
vaudrait ma collection actuelle aux prix d'il y a trois mois », pas à « combien
valait ma collection il y a trois mois ». Reconstituer la seconde exigerait un
historique des possessions que le schéma ne garde pas.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from sqlalchemy import Date, and_, cast, func, literal, select
from sqlalchemy.dialects.postgresql import INTERVAL
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    Card,
    CollectionItem,
    Expansion,
    PriceSnapshot,
    PriceSource,
    Printing,
    Tcg,
)


def _latest_quotes(as_of: date, source: str | None):
    """Sous-requête : la cote la plus récente par impression, à une date donnée.

    `DISTINCT ON` est propre à PostgreSQL et fait exactement ce qu'il faut ici :
    une ligne par impression, celle du relevé le plus récent. L'équivalent
    portable — fenêtre + filtre sur le rang — coûterait un tri de plus pour le
    même résultat.
    """
    stmt = (
        select(
            PriceSnapshot.printing_id,
            PriceSnapshot.amount,
            PriceSnapshot.currency,
            PriceSnapshot.price_type,
            PriceSnapshot.observed_on,
            PriceSource.code.label("source_code"),
        )
        .join(PriceSource, PriceSource.id == PriceSnapshot.source_id)
        .where(PriceSnapshot.observed_on <= as_of)
        .order_by(PriceSnapshot.printing_id, PriceSnapshot.observed_on.desc())
        .distinct(PriceSnapshot.printing_id)
    )
    if source:
        stmt = stmt.where(PriceSource.code == source)
    return stmt.subquery()


def _holdings(tcg: str | None):
    """Sous-requête : quantité possédée par impression."""
    stmt = select(
        CollectionItem.printing_id.label("printing_id"),
        func.sum(CollectionItem.quantity).label("quantity"),
    ).group_by(CollectionItem.printing_id)
    if tcg:
        stmt = (
            stmt.join(Printing, Printing.id == CollectionItem.printing_id)
            .join(Card, Card.id == Printing.card_id)
            .join(Expansion, Expansion.id == Card.expansion_id)
            .join(Tcg, Tcg.id == Expansion.tcg_id)
            .where(Tcg.code == tcg)
        )
    return stmt.subquery()


async def valuation(
    session: AsyncSession,
    *,
    as_of: date | None = None,
    tcg: str | None = None,
    source: str | None = None,
) -> dict[str, Any]:
    """Valeur de marché de la collection à une date."""
    jour = as_of or date.today()
    cotes = _latest_quotes(jour, source)
    possessions = _holdings(tcg)

    lignes = (
        await session.execute(
            select(
                cotes.c.currency,
                cotes.c.source_code,
                func.sum(cotes.c.amount * possessions.c.quantity).label("total"),
                func.sum(possessions.c.quantity).label("quantity"),
                func.count().label("printings"),
                func.min(cotes.c.observed_on).label("oldest"),
                func.max(cotes.c.observed_on).label("newest"),
            )
            .select_from(possessions)
            .join(cotes, cotes.c.printing_id == possessions.c.printing_id)
            .group_by(cotes.c.currency, cotes.c.source_code)
        )
    ).all()

    # Ce qui n'a aucune cote : compté à part, jamais valorisé à zéro.
    sans_cote = (
        await session.execute(
            select(
                func.coalesce(func.sum(possessions.c.quantity), 0),
                func.count(),
            )
            .select_from(possessions)
            .outerjoin(cotes, cotes.c.printing_id == possessions.c.printing_id)
            .where(cotes.c.printing_id.is_(None))
        )
    ).one()

    return {
        "as_of": jour,
        "totals": [
            {
                "currency": ligne.currency,
                "source": ligne.source_code,
                "total": ligne.total,
                "quantity": int(ligne.quantity),
                "printings": int(ligne.printings),
                "oldest_quote": ligne.oldest,
                "newest_quote": ligne.newest,
            }
            for ligne in lignes
        ],
        "uncovered_quantity": int(sans_cote[0]),
        "uncovered_printings": int(sans_cote[1]),
    }


async def valuation_history(
    session: AsyncSession,
    *,
    days: int,
    tcg: str | None = None,
    source: str | None = None,
    step_days: int = 1,
) -> list[dict[str, Any]]:
    """Série temporelle de la valeur, sur les `days` derniers jours.

    Rappel de l'en-tête : les cotes passées sont appliquées à la collection
    **actuelle**. C'est une courbe de prix, pas une reconstitution de
    patrimoine.

    La série est construite en une seule requête : une jointure latérale
    prend, pour chaque jour et chaque impression, le dernier relevé connu à
    cette date. Boucler côté Python aurait multiplié les allers-retours par le
    nombre de jours — 365 requêtes pour une courbe sur un an.
    """
    fin = date.today()
    debut = fin - timedelta(days=days)
    possessions = _holdings(tcg)

    # Le pas doit être un INTERVAL, pas une chaîne : PostgreSQL n'a pas de
    # surcharge `generate_series(date, date, varchar)`. Et la série sort en
    # timestamp, d'où le cast en DATE pour rester homogène avec `observed_on`.
    jours = select(
        cast(
            func.generate_series(
                cast(literal(debut), Date),
                cast(literal(fin), Date),
                cast(literal(f"{step_days} days"), INTERVAL),
            ),
            Date,
        ).label("jour")
    ).subquery()

    derniere = (
        select(
            PriceSnapshot.amount,
            PriceSnapshot.currency,
        )
        .join(PriceSource, PriceSource.id == PriceSnapshot.source_id)
        .where(
            and_(
                PriceSnapshot.printing_id == possessions.c.printing_id,
                PriceSnapshot.observed_on <= jours.c.jour,
            )
        )
        .order_by(PriceSnapshot.observed_on.desc())
        .limit(1)
    )
    if source:
        derniere = derniere.where(PriceSource.code == source)
    derniere = derniere.lateral("derniere")

    stmt = (
        select(
            jours.c.jour,
            derniere.c.currency,
            func.sum(derniere.c.amount * possessions.c.quantity).label("total"),
            func.count().label("printings"),
        )
        .select_from(jours)
        .join(possessions, literal(True))
        .join(derniere, literal(True))
        .group_by(jours.c.jour, derniere.c.currency)
        .order_by(jours.c.jour)
    )

    return [
        {
            "date": ligne.jour,
            "currency": ligne.currency,
            "total": ligne.total,
            "printings": int(ligne.printings),
        }
        for ligne in (await session.execute(stmt)).all()
    ]

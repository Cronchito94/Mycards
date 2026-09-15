"""Recherche de cartes.

Toute la logique SQL vit ici, jamais dans les routes : c'est la partie qu'on
voudra optimiser, et elle doit rester lisible d'un seul tenant.

Trois choses à savoir avant de la modifier :

**1. Le terme est normalisé par PostgreSQL, pas par Python.**
`card_localization.name_normalized` est une colonne générée
(`lower(immutable_unaccent(name))`). Normaliser la requête côté Python avec
un `unicodedata` maison finirait tôt ou tard par diverger du dictionnaire
`unaccent` de la base, et la recherche raterait silencieusement des cartes.
On paie donc un aller-retour SQL pour normaliser avec *la même fonction*.

**2. Deux opérateurs, un seul index.** `%` (trigram) absorbe les fautes de
frappe, `LIKE '%…%'` attrape les sous-chaînes que le trigram note trop bas
(« feu » dans « Dracaufeu »). Les deux s'appuient sur le même index GIN
`ix_card_localization_normalized_trgm`, vérifié par EXPLAIN.

**3. On cherche des localisations, on renvoie des cartes.** Le `GROUP BY` sur
`card.id` est ce qui fait que « dracaufeu » et « charizard » retombent sur la
même carte — c'est le critère de fin du lot.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy import Select, and_, case, func, literal, or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models import Card, CardLocalization, Expansion, Printing, Rarity, Tcg
from app.models.tcg import CardType

# En deçà, une recherche n'a pas de sens et ramènerait la moitié du référentiel.
MIN_QUERY_LENGTH = 2

# Sous ce seuil, l'index trigram (`%`) ne peut pas s'appuyer sur des trigrammes
# complets et PostgreSQL retombe sur un parcours séquentiel. On le signale dans
# la doc plutôt que d'interdire : 45 000 lignes se parcourent en quelques ms.
TRIGRAM_MIN_LENGTH = 3


@dataclass(frozen=True)
class CardFilters:
    """Filtres applicables à une recherche ou à un simple parcours."""

    tcg: str | None = None
    expansion: str | None = None
    rarity: str | None = None
    card_type: str | None = None
    language: str | None = None


def escape_like(terme: str) -> str:
    """Neutralise les jokers LIKE saisis par l'utilisateur.

    Sans ça, chercher « 100% » ou « pika_ » ferait passer le joker dans le
    motif et renverrait n'importe quoi. L'antislash est échappé en premier,
    sinon il ré-échapperait les échappements ajoutés juste après.
    """
    return terme.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


async def normalize_term(session: AsyncSession, terme: str) -> str:
    """Normalise avec la fonction de la base, pas une réimplémentation.

    Voir le point 1 de l'en-tête du module : c'est volontairement un
    aller-retour SQL.
    """
    resultat = await session.execute(
        select(func.lower(func.immutable_unaccent(terme)))
    )
    return resultat.scalar_one()


def _apply_filters(stmt: Select[Any], filtres: CardFilters) -> Select[Any]:
    """Applique les filtres sur une requête déjà jointe à `card`.

    Les filtres portent sur les **codes**, jamais sur les libellés : un libellé
    est traduit, un code ne l'est pas. Filtrer sur « Peu Commune » marcherait en
    français et nulle part ailleurs.
    """
    if filtres.expansion or filtres.tcg:
        stmt = stmt.join(Expansion, Expansion.id == Card.expansion_id)
        if filtres.expansion:
            stmt = stmt.where(Expansion.code == filtres.expansion)
        if filtres.tcg:
            stmt = stmt.join(Tcg, Tcg.id == Expansion.tcg_id).where(
                Tcg.code == filtres.tcg
            )
    if filtres.rarity:
        stmt = stmt.join(Rarity, Rarity.id == Card.rarity_id).where(
            Rarity.code == filtres.rarity
        )
    if filtres.card_type:
        stmt = stmt.join(CardType, CardType.id == Card.card_type_id).where(
            CardType.code == filtres.card_type
        )
    return stmt


def _match_clause(terme: str, filtres: CardFilters) -> Any:
    """Condition de correspondance sur une localisation.

    `%` et `LIKE` sont complémentaires : le premier tolère les fautes, le
    second attrape les sous-chaînes. Les deux passent par l'index GIN.
    """
    motif = f"%{escape_like(terme)}%"
    condition = or_(
        CardLocalization.name_normalized.op("%")(terme),
        CardLocalization.name_normalized.like(motif, escape="\\"),
    )
    if filtres.language:
        condition = and_(condition, CardLocalization.language == filtres.language)
    return condition


def _score_expression(terme: str) -> Any:
    """Score de pertinence, entre 0 et 1.

    Construit en `GREATEST` plutôt qu'en `CASE` exclusif : une correspondance
    exacte vaut toujours 1, un préfixe au moins 0,9, une sous-chaîne au moins
    0,8 — et une correspondance floue qui dépasserait ces planchers garde sa
    valeur au lieu d'être rabotée.
    """
    echappe = escape_like(terme)
    return func.greatest(
        func.similarity(CardLocalization.name_normalized, terme),
        case(
            (CardLocalization.name_normalized == terme, literal(1.0)),
            (
                CardLocalization.name_normalized.like(f"{echappe}%", escape="\\"),
                literal(0.9),
            ),
            (
                CardLocalization.name_normalized.like(f"%{echappe}%", escape="\\"),
                literal(0.8),
            ),
            else_=literal(0.0),
        ),
    )


async def search_cards(
    session: AsyncSession,
    *,
    query: str | None,
    filtres: CardFilters,
    page: int,
    page_size: int,
) -> tuple[list[Card], dict[int, float], int]:
    """Cherche des cartes et renvoie (cartes de la page, scores, total).

    Sans `query`, c'est un simple parcours filtré, trié par extension puis
    numéro — ce dont le front a besoin pour afficher un set entier.
    """
    offset = (page - 1) * page_size

    if query:
        terme = await normalize_term(session, query)
        score = func.max(_score_expression(terme)).label("score")

        base = (
            select(Card.id, score)
            .join(CardLocalization, CardLocalization.card_id == Card.id)
            .where(_match_clause(terme, filtres))
            .group_by(Card.id)
        )
        base = _apply_filters(base, filtres)
        # Le tri secondaire sur l'id rend la pagination déterministe : sans lui,
        # deux cartes de même score peuvent changer de page d'un appel à l'autre.
        stmt = base.order_by(text("score DESC"), Card.id).limit(page_size).offset(offset)

        lignes = (await session.execute(stmt)).all()
        scores = {ligne.id: float(ligne.score) for ligne in lignes}
        ids = list(scores)

        comptage = select(func.count(func.distinct(Card.id))).join(
            CardLocalization, CardLocalization.card_id == Card.id
        ).where(_match_clause(terme, filtres))
        comptage = _apply_filters(comptage, filtres)
        total = (await session.execute(comptage)).scalar_one()
    else:
        base = select(Card.id)
        base = _apply_filters(base, filtres)
        stmt = (
            base.join(Expansion, Expansion.id == Card.expansion_id, isouter=False)
            .order_by(Expansion.release_date.desc().nullslast(), Card.number_sort, Card.id)
            .limit(page_size)
            .offset(offset)
            if not (filtres.expansion or filtres.tcg)
            else base.order_by(Card.number_sort, Card.id).limit(page_size).offset(offset)
        )
        ids = list((await session.execute(stmt)).scalars())
        scores = {}

        comptage = _apply_filters(select(func.count(Card.id)), filtres)
        total = (await session.execute(comptage)).scalar_one()

    if not ids:
        return [], {}, total

    cartes = await load_cards(session, ids, with_printings=False)
    # `load_cards` renvoie dans l'ordre de la base ; on rétablit l'ordre du
    # classement, que seule la première requête connaît.
    par_id = {c.id: c for c in cartes}
    return [par_id[i] for i in ids if i in par_id], scores, total


async def load_cards(
    session: AsyncSession, ids: list[int], *, with_printings: bool
) -> list[Card]:
    """Charge des cartes avec leurs relations, sans requête par carte.

    `selectinload` émet une requête par relation pour tout le lot — pas une par
    carte. Sans ça, une page de 20 résultats déclencherait une centaine
    d'allers-retours.
    """
    options = [
        selectinload(Card.expansion).selectinload(Expansion.names),
        selectinload(Card.rarity),
        selectinload(Card.card_type),
        selectinload(Card.localizations),
    ]
    if with_printings:
        options.append(selectinload(Card.printings).selectinload(Printing.finish))

    stmt = select(Card).where(Card.id.in_(ids)).options(*options)
    return list((await session.execute(stmt)).scalars().unique())


async def get_card(session: AsyncSession, card_id: int) -> Card | None:
    """Une carte, avec toutes ses impressions."""
    cartes = await load_cards(session, [card_id], with_printings=True)
    return cartes[0] if cartes else None

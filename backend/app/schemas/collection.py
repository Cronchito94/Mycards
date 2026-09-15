"""Schémas de la collection.

Convention reprise du lot 3 : ce qui est traduit sort en dictionnaire indexé
par langue, jamais résolu dans une langue choisie par le serveur.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.models.enums import CardCondition
from app.schemas.catalog import ExpansionOut, VocabularyOut


class CardRef(BaseModel):
    """La carte derrière un exemplaire, réduite à ce qu'il faut pour l'afficher.

    Porte `image_url` : le front du lot 6 doit pouvoir montrer une miniature,
    pas une ligne de texte. C'est l'image **de la langue de l'impression
    possédée** — une carte japonaise montre l'image japonaise.
    """

    id: int
    number: str
    names: dict[str, str] = Field(default_factory=dict)
    image_url: str | None = None
    rarity: VocabularyOut | None = None
    expansion: ExpansionOut


class PrintingRef(BaseModel):
    """L'impression possédée : c'est elle qui distingue deux Pikachu."""

    id: int
    language: str
    finish: VocabularyOut
    card: CardRef


class LotOut(BaseModel):
    """Un lot d'achat : une quantité, un état, et son prix de revient propre."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    quantity: int
    condition: CardCondition
    # **Prix unitaire**, pas prix du lot.
    unit_purchase_price: Decimal | None = None
    purchase_currency: str | None = None
    purchase_date: date | None = None
    notes: str | None = None


class HoldingOut(BaseModel):
    """Une entrée de collection : une impression précise et tout ce qu'on en a.

    Dix Pikachu dont cinq identiques donnent une entrée à 5 et plusieurs
    entrées à 1 — jamais une entrée « Pikachu ×10 ».
    """

    printing: PrintingRef
    quantity: int
    by_condition: dict[str, int] = Field(default_factory=dict)
    lots: list[LotOut] = Field(default_factory=list)


class ItemCreate(BaseModel):
    """Ajout d'exemplaires.

    Sans prix d'achat, l'ajout **fusionne** avec une ligne existante de même
    impression et même état. Avec un prix, il crée un lot distinct : ce prix
    de revient lui est propre et le fusionner le perdrait.
    """

    printing_id: int
    quantity: int = Field(1, ge=1)
    condition: CardCondition = CardCondition.NEAR_MINT
    unit_purchase_price: Decimal | None = Field(None, ge=0)
    purchase_currency: str | None = Field(None, min_length=3, max_length=3)
    purchase_date: date | None = None
    notes: str | None = None


class ItemUpdate(BaseModel):
    quantity: int | None = Field(None, ge=1)
    condition: CardCondition | None = None
    unit_purchase_price: Decimal | None = Field(None, ge=0)
    purchase_currency: str | None = Field(None, min_length=3, max_length=3)
    purchase_date: date | None = None
    notes: str | None = None


class SaleCreate(BaseModel):
    """Enregistrement d'une vente.

    Retire les exemplaires de la collection. Sans `collection_item_id`, les
    exemplaires sont pris sur les lots les plus anciens d'abord.
    """

    printing_id: int
    quantity: int = Field(1, ge=1)
    # Obligatoire, contrairement au prix d'achat : une vente sans montant n'a
    # pas de sens.
    unit_sale_price: Decimal = Field(ge=0)
    sale_currency: str = Field("EUR", min_length=3, max_length=3)
    sale_date: date | None = None
    platform: str | None = Field(
        None, description="Nom libre ; normalisé pour éviter les doublons de casse."
    )
    condition: CardCondition | None = None
    collection_item_id: int | None = None
    notes: str | None = None


class SaleOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    quantity: int
    condition: CardCondition
    unit_sale_price: Decimal
    sale_currency: str
    sale_date: date | None = None
    platform: str | None = None
    notes: str | None = None
    printing: PrintingRef


class MoneyTotal(BaseModel):
    """Un total **par devise**. Il n'y a jamais de total toutes devises
    confondues : aucune conversion n'est faite."""

    currency: str
    total: Decimal
    # Nombre de cartes physiques couvertes par ce total.
    quantity: int


class PlatformTotal(MoneyTotal):
    platform: str


class CollectionStats(BaseModel):
    """Les trois compteurs, plus l'argent.

    `items` compte les cartes physiques, `distinct_printings` les versions
    différentes, `distinct_cards` les cartes du référentiel. Trois questions
    différentes, trois réponses différentes.
    """

    items: int
    distinct_printings: int
    distinct_cards: int
    by_condition: dict[str, int] = Field(default_factory=dict)
    purchase_value: list[MoneyTotal] = Field(default_factory=list)
    # Les exemplaires sans prix saisi ne valent pas zéro : leur prix est
    # inconnu, ce qui est une information différente.
    items_without_price: int = 0
    sales_total: list[MoneyTotal] = Field(default_factory=list)
    sales_by_platform: list[PlatformTotal] = Field(default_factory=list)


class ExpansionRef(BaseModel):
    id: int
    code: str
    release_date: date | None = None
    names: dict[str, str] = Field(default_factory=dict)


class CompletionOut(BaseModel):
    """Complétion d'une extension.

    Deux dénominateurs : `official` est le total imprimé sur la carte,
    `total` inclut les cartes secrètes. `165/165` et `165/207` ne racontent
    pas la même histoire.
    """

    expansion: ExpansionRef
    owned: int
    official: int | None = None
    total: int | None = None
    percent_official: float | None = None
    percent_total: float | None = None


def build_printing_ref(printing: Any) -> PrintingRef:
    """Assemble la référence d'impression depuis l'ORM.

    L'image retenue est celle de la **langue de l'impression** : comparer une
    carte française à une image anglaise n'aurait aucun sens à l'affichage —
    ni pour le scan photo du lot 7.
    """
    carte = printing.card
    localisations = {loc.language: loc for loc in carte.localizations}
    locale = localisations.get(printing.language)
    return PrintingRef(
        id=printing.id,
        language=printing.language,
        finish=VocabularyOut.model_validate(printing.finish),
        card=CardRef(
            id=carte.id,
            number=carte.number,
            names={lang: loc.name for lang, loc in localisations.items()},
            image_url=locale.image_url if locale else None,
            rarity=VocabularyOut.model_validate(carte.rarity) if carte.rarity else None,
            expansion=ExpansionOut.from_expansion(carte.expansion),
        ),
    )

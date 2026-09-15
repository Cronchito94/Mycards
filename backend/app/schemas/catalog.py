"""Schémas de sortie du référentiel.

Parti pris d'exposition : tout ce qui est traduit sort sous forme de
dictionnaire indexé par langue (`{"fr": "Dracaufeu", "en": "Charizard"}`),
jamais résolu dans une langue choisie par le serveur. C'est le client qui sait
quelle langue afficher, et une même réponse sert alors toutes les langues.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class TcgOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    code: str
    name: str


class VocabularyOut(BaseModel):
    """Une entrée de vocabulaire : rareté, finition, type de carte.

    `code` est invariant par langue et sert aux filtres ; `labels` porte les
    libellés traduits. Un filtre s'écrit toujours sur le code.
    """

    model_config = ConfigDict(from_attributes=True)

    id: int
    code: str
    labels: dict[str, str] = Field(default_factory=dict)


class ExpansionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    code: str
    series: str | None = None
    release_date: date | None = None
    card_count_official: int | None = None
    card_count_total: int | None = None
    symbol_url: str | None = None
    names: dict[str, str] = Field(default_factory=dict)
    logo_urls: dict[str, str] = Field(default_factory=dict)

    @classmethod
    def from_expansion(cls, expansion: Any) -> ExpansionOut:
        """Le symbole est universel chez TCGdex, le logo est par langue — d'où
        un champ simple pour l'un et un dictionnaire pour l'autre."""
        return cls(
            id=expansion.id,
            code=expansion.code,
            series=expansion.series,
            release_date=expansion.release_date,
            card_count_official=expansion.card_count_official,
            card_count_total=expansion.card_count_total,
            symbol_url=expansion.symbol_url,
            names={n.language: n.name for n in expansion.names},
            logo_urls={n.language: n.logo_url for n in expansion.names if n.logo_url},
        )


class PrintingOut(BaseModel):
    """Une impression : c'est à ce niveau que se rattachent prix et exemplaires."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    language: str
    finish: VocabularyOut
    external_ids: dict[str, Any] = Field(default_factory=dict)
    attributes: dict[str, Any] = Field(default_factory=dict)


class LocalizationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    language: str
    name: str
    image_url: str | None = None
    attributes: dict[str, Any] = Field(default_factory=dict)


class CardSummary(BaseModel):
    """Carte en résultat de recherche : de quoi afficher une vignette."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    number: str
    number_sort: int | None = None
    illustrator: str | None = None
    names: dict[str, str] = Field(default_factory=dict)
    images: dict[str, str] = Field(default_factory=dict)
    rarity: VocabularyOut | None = None
    card_type: VocabularyOut | None = None
    expansion: ExpansionOut
    # Présent uniquement sur une recherche par nom. 1.0 = correspondance exacte.
    score: float | None = None

    @classmethod
    def from_card(cls, card: Any, score: float | None = None) -> CardSummary:
        """Assemble depuis l'ORM.

        Les noms et images sont recomposés en dictionnaires par langue depuis
        `card_localization` : le client choisit la langue, pas le serveur.
        """
        return cls(
            id=card.id,
            number=card.number,
            number_sort=card.number_sort,
            illustrator=card.illustrator,
            names={loc.language: loc.name for loc in card.localizations},
            images={
                loc.language: loc.image_url
                for loc in card.localizations
                if loc.image_url
            },
            rarity=VocabularyOut.model_validate(card.rarity) if card.rarity else None,
            card_type=(
                VocabularyOut.model_validate(card.card_type) if card.card_type else None
            ),
            expansion=ExpansionOut.from_expansion(card.expansion),
            # Arrondi : `similarity` renvoie un float32, dont la conversion
            # produit des queues de décimales sans aucun sens (0.5833333134…).
            score=None if score is None else round(score, 4),
        )


class CardDetail(CardSummary):
    """Carte détaillée : toutes ses localisations et **toutes ses impressions**."""

    attributes: dict[str, Any] = Field(default_factory=dict)
    external_ids: dict[str, Any] = Field(default_factory=dict)
    localizations: list[LocalizationOut] = Field(default_factory=list)
    printings: list[PrintingOut] = Field(default_factory=list)

    @classmethod
    def from_card(cls, card: Any) -> CardDetail:
        base = CardSummary.from_card(card).model_dump()
        base.update(
            attributes=card.attributes or {},
            external_ids=card.external_ids or {},
            localizations=[
                LocalizationOut.model_validate(loc)
                for loc in sorted(card.localizations, key=lambda x: x.language)
            ],
            # Triées par langue puis finition : une liste stable est plus
            # facile à lire pour un humain comme pour un test.
            printings=[
                PrintingOut.model_validate(p)
                for p in sorted(card.printings, key=lambda x: (x.language, x.finish.code))
            ],
        )
        return cls(**base)

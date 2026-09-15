"""Modèles ORM.

Ce module importe tous les modèles pour qu'ils soient enregistrés sur
`Base.metadata` — c'est ce qui permet à l'autogénération Alembic de les voir.
"""

from app.models.catalog import (
    Card,
    CardLocalization,
    Expansion,
    ExpansionName,
    Printing,
)
from app.models.collection import CollectionItem, WatchedPrinting
from app.models.enums import CardCondition
from app.models.imports import ImportCheckpoint
from app.models.pricing import PriceSnapshot, PriceSource
from app.models.tcg import CardType, Finish, Rarity, Tcg

__all__ = [
    "Card",
    "CardCondition",
    "CardLocalization",
    "CardType",
    "CollectionItem",
    "Expansion",
    "ExpansionName",
    "Finish",
    "ImportCheckpoint",
    "PriceSnapshot",
    "PriceSource",
    "Printing",
    "Rarity",
    "Tcg",
    "WatchedPrinting",
]

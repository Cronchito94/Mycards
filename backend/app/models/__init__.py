"""Modèles ORM.

Ce module importe tous les modèles pour qu'ils soient enregistrés sur
`Base.metadata` — c'est ce qui permet à l'autogénération Alembic de les voir.
"""

from app.models.catalog import Card, CardName, Expansion, Printing
from app.models.collection import CollectionItem, WatchedPrinting
from app.models.enums import CardCondition
from app.models.pricing import PriceSnapshot, PriceSource
from app.models.tcg import Finish, Rarity, Tcg

__all__ = [
    "Card",
    "CardCondition",
    "CardName",
    "CollectionItem",
    "Expansion",
    "Finish",
    "PriceSnapshot",
    "PriceSource",
    "Printing",
    "Rarity",
    "Tcg",
    "WatchedPrinting",
]

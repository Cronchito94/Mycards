"""Énumérations du domaine.

Principe de découpage, important pour la suite :

- Ce qui est **fermé et normalisé** devient un `ENUM` PostgreSQL (typé, validé
  par la base). C'est le cas de l'état d'une carte : l'échelle Cardmarket est
  stable depuis des années.
- Ce qui est **ouvert et propre à chaque jeu** devient une table de référence
  (`rarity`, `finish`, `price_source`). On ne connaîtra les vraies valeurs
  Pokémon qu'au lot 2, et Riftbound en apportera d'autres : il ne faut pas
  qu'ajouter une finition impose une migration.
"""

import enum


class CardCondition(enum.Enum):
    """État d'un exemplaire, échelle Cardmarket.

    Ordonnée du meilleur au moins bon. La valeur stockée en base est le code
    court (`MT`, `NM`, …), pas le nom Python.
    """

    MINT = "MT"
    NEAR_MINT = "NM"
    EXCELLENT = "EX"
    GOOD = "GD"
    LIGHT_PLAYED = "LP"
    PLAYED = "PL"
    POOR = "PO"

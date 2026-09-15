"""Niveau jeu : le TCG et ses vocabulaires propres (raretés, finitions, types).

Les trois vocabulaires suivent le même patron : un `code` invariant qui sert de
clé d'upsert à l'import, et des `labels` par langue. Ce découpage vient d'un
constat fait sur les données réelles : TCGdex renvoie « Peu Commune » en
français et « Uncommon » en anglais pour *la même* rareté. Un libellé unique
aurait figé la première langue importée.
"""

from typing import TYPE_CHECKING, Any

from sqlalchemy import ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.db.mixins import TimestampMixin

if TYPE_CHECKING:
    from app.models.catalog import Card, Expansion, Printing


class Tcg(TimestampMixin, Base):
    """Un jeu de cartes : Pokémon, Riftbound, …

    Racine de tout le modèle : une collection est toujours rattachée à un TCG,
    et les vocabulaires (raretés, finitions, types de carte) sont propres à
    chaque jeu.
    """

    __tablename__ = "tcg"

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(32), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(128), nullable=False)

    expansions: Mapped[list["Expansion"]] = relationship(
        back_populates="tcg", cascade="all, delete-orphan"
    )
    rarities: Mapped[list["Rarity"]] = relationship(
        back_populates="tcg", cascade="all, delete-orphan"
    )
    finishes: Mapped[list["Finish"]] = relationship(
        back_populates="tcg", cascade="all, delete-orphan"
    )
    card_types: Mapped[list["CardType"]] = relationship(
        back_populates="tcg", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:
        return f"<Tcg {self.code}>"


class Rarity(TimestampMixin, Base):
    """Rareté, propre à un TCG.

    Table de référence plutôt qu'`ENUM` : Pokémon en compte des dizaines et la
    liste s'allonge à chaque extension — l'API en renvoie 30 rien qu'en
    français. Riftbound a la sienne (Common, Uncommon, Rare, Epic,
    Overnumbered, Alternate Art). L'import crée les raretés au fil de l'eau.

    ⚠️ « Alternate Art » est listée par la source Riftbound comme une *rareté*
    alors que c'est une variante d'impression. C'est un problème de mapping à
    l'import, pas de schéma : la finition correspondante va dans `finish`.
    """

    __tablename__ = "rarity"
    __table_args__ = (UniqueConstraint("tcg_id", "code", name="uq_rarity_tcg_code"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    tcg_id: Mapped[int] = mapped_column(
        ForeignKey("tcg.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # Code invariant, en anglais quand la source le permet ("Rare Holo").
    # Sert de clé d'upsert : il ne doit surtout pas dépendre de la langue.
    code: Mapped[str] = mapped_column(String(64), nullable=False)
    # Libellés par langue : {"fr": "Peu Commune", "en": "Uncommon"}.
    labels: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default="{}"
    )
    # Ordre d'affichage / de tri ; renseigné à la main, la source ne le donne pas.
    sort_order: Mapped[int | None] = mapped_column(Integer)

    tcg: Mapped["Tcg"] = relationship(back_populates="rarities")
    cards: Mapped[list["Card"]] = relationship(back_populates="rarity")

    def __repr__(self) -> str:
        return f"<Rarity {self.code}>"


class Finish(TimestampMixin, Base):
    """Finition d'une impression : normale, reverse holo, holo, 1st edition…

    **C'est le point délicat du modèle.** Une reverse holo et une normale sont
    la même CARTE mais deux IMPRESSIONS distinctes, avec deux prix différents.

    Vocabulaire réellement observé côté Pokémon (TCGdex) : `normal`, `reverse`,
    `holo`, `firstEdition`, `wPromo`. Côté Riftbound, la source distingue au
    moins l'art alternatif. Table de référence et non `ENUM` : une nouvelle
    finition s'ajoute par un `INSERT`.
    """

    __tablename__ = "finish"
    __table_args__ = (UniqueConstraint("tcg_id", "code", name="uq_finish_tcg_code"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    tcg_id: Mapped[int] = mapped_column(
        ForeignKey("tcg.id", ondelete="CASCADE"), nullable=False, index=True
    )
    code: Mapped[str] = mapped_column(String(64), nullable=False)
    labels: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default="{}"
    )
    sort_order: Mapped[int | None] = mapped_column(Integer)

    tcg: Mapped["Tcg"] = relationship(back_populates="finishes")
    printings: Mapped[list["Printing"]] = relationship(back_populates="finish")

    def __repr__(self) -> str:
        return f"<Finish {self.code}>"


class CardType(TimestampMixin, Base):
    """Nature de la carte dans son jeu.

    Pokémon : Pokémon, Dresseur, Énergie (trois valeurs, stables).
    Riftbound : Unit, Spell, Champion Unit, Legend, Gear, Battlefield, Rune,
    Signature Spell (huit valeurs observées sur les 670 cartes publiées).

    Deux jeux, deux vocabulaires sans recoupement : c'est précisément pourquoi
    la table est rattachée à `TCG` plutôt que globale, et pourquoi ce n'est pas
    un `ENUM`. Le filtre « ne montre que mes champions » du lot 3 s'appuiera
    dessus, d'où une vraie table plutôt qu'une clé de `card.attributes`.
    """

    __tablename__ = "card_type"
    __table_args__ = (
        UniqueConstraint("tcg_id", "code", name="uq_card_type_tcg_code"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    tcg_id: Mapped[int] = mapped_column(
        ForeignKey("tcg.id", ondelete="CASCADE"), nullable=False, index=True
    )
    code: Mapped[str] = mapped_column(String(64), nullable=False)
    labels: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default="{}"
    )
    sort_order: Mapped[int | None] = mapped_column(Integer)

    tcg: Mapped["Tcg"] = relationship(back_populates="card_types")
    cards: Mapped[list["Card"]] = relationship(back_populates="card_type")

    def __repr__(self) -> str:
        return f"<CardType {self.code}>"

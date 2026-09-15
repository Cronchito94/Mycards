"""Niveau jeu : le TCG et ses vocabulaires propres (raretés, finitions)."""

from typing import TYPE_CHECKING

from sqlalchemy import ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.db.mixins import TimestampMixin

if TYPE_CHECKING:
    from app.models.catalog import Card, Expansion, Printing


class Tcg(TimestampMixin, Base):
    """Un jeu de cartes : Pokémon, Riftbound, …

    Racine de tout le modèle : une collection est toujours rattachée à un TCG,
    et les vocabulaires (raretés, finitions) sont propres à chaque jeu.
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

    def __repr__(self) -> str:
        return f"<Tcg {self.code}>"


class Rarity(TimestampMixin, Base):
    """Rareté, propre à un TCG.

    Table de référence plutôt qu'`ENUM` : Pokémon en compte des dizaines
    (Common, Rare Holo, Ultra Rare, Illustration Rare…), la liste s'allonge à
    chaque extension, et Riftbound aura la sienne. L'import du lot 2 crée les
    raretés au fil de l'eau ; aucune migration n'est nécessaire pour en ajouter.
    """

    __tablename__ = "rarity"
    __table_args__ = (UniqueConstraint("tcg_id", "code", name="uq_rarity_tcg_code"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    tcg_id: Mapped[int] = mapped_column(
        ForeignKey("tcg.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # Code tel que renvoyé par la source (ex. "Rare Holo"), sert de clé d'upsert.
    code: Mapped[str] = mapped_column(String(64), nullable=False)
    label: Mapped[str | None] = mapped_column(String(128))
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

    Table de référence et non `ENUM`, pour la même raison que `Rarity`, mais
    avec un enjeu plus fort : on ne connaîtra le vocabulaire réel de TCGdex
    qu'au lot 2. Le schéma doit accepter ce qu'on y trouvera sans migration.
    """

    __tablename__ = "finish"
    __table_args__ = (UniqueConstraint("tcg_id", "code", name="uq_finish_tcg_code"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    tcg_id: Mapped[int] = mapped_column(
        ForeignKey("tcg.id", ondelete="CASCADE"), nullable=False, index=True
    )
    code: Mapped[str] = mapped_column(String(64), nullable=False)
    label: Mapped[str | None] = mapped_column(String(128))
    sort_order: Mapped[int | None] = mapped_column(Integer)

    tcg: Mapped["Tcg"] = relationship(back_populates="finishes")
    printings: Mapped[list["Printing"]] = relationship(back_populates="finish")

    def __repr__(self) -> str:
        return f"<Finish {self.code}>"

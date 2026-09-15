"""Référentiel des cartes : extension, carte, noms localisés, impressions.

Hiérarchie, du plus abstrait au plus concret :

    TCG → EXTENSION → CARD ─┬→ CARD_NAME  (un nom par langue)
                            └→ PRINTING   (finition + langue) → prix, exemplaires

La séparation CARD / PRINTING est la décision structurante du modèle :
« Dracaufeu 4/102 » est **une** carte, mais sa version normale anglaise et sa
reverse holo française sont **deux** impressions, avec deux cotes distinctes.
"""

from datetime import date
from typing import TYPE_CHECKING, Any

from sqlalchemy import (
    Computed,
    Date,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.db.mixins import TimestampMixin

if TYPE_CHECKING:
    from app.models.collection import CollectionItem, WatchedPrinting
    from app.models.pricing import PriceSnapshot
    from app.models.tcg import Finish, Rarity, Tcg


class Expansion(TimestampMixin, Base):
    """Une extension / un set : « Épée et Bouclier — Ténèbres Embrasées »."""

    __tablename__ = "expansion"
    __table_args__ = (
        UniqueConstraint("tcg_id", "code", name="uq_expansion_tcg_code"),
        # Index GIN sur les identifiants externes : l'import du lot 2 doit
        # pouvoir retrouver une extension par son id TCGdex sans scan complet.
        Index("ix_expansion_external_ids", "external_ids", postgresql_using="gin"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    tcg_id: Mapped[int] = mapped_column(
        ForeignKey("tcg.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # Code de la source (TCGdex : "swsh3"). Clé naturelle d'upsert avec tcg_id.
    code: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(String(256), nullable=False)
    # Série parente ("Sword & Shield"), utile pour regrouper à l'affichage.
    series: Mapped[str | None] = mapped_column(String(256))
    release_date: Mapped[date | None] = mapped_column(Date)

    # Deux compteurs : le total officiel imprimé sur la carte (le 102 de
    # « 4/102 ») et le nombre réel de cartes du set, secrètes comprises.
    # La complétion du lot 4 a besoin des deux.
    card_count_official: Mapped[int | None] = mapped_column(Integer)
    card_count_total: Mapped[int | None] = mapped_column(Integer)

    logo_url: Mapped[str | None] = mapped_column(Text)
    symbol_url: Mapped[str | None] = mapped_column(Text)

    # Correspondance vers les sources externes, ex :
    # {"tcgdex": "swsh3", "pokemontcgio": "swsh3"}
    external_ids: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default="{}"
    )

    tcg: Mapped["Tcg"] = relationship(back_populates="expansions")
    cards: Mapped[list["Card"]] = relationship(
        back_populates="expansion", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:
        return f"<Expansion {self.code}>"


class Card(TimestampMixin, Base):
    """La carte au sens abstrait : son numéro dans l'extension, sa rareté,
    son illustration.

    Aucun prix ici, jamais : un prix s'attache à une `Printing`.
    Aucun nom non plus : les noms vivent dans `CardName`, un par langue.
    """

    __tablename__ = "card"
    __table_args__ = (
        UniqueConstraint("expansion_id", "number", name="uq_card_expansion_number"),
        Index("ix_card_external_ids", "external_ids", postgresql_using="gin"),
        # Tri d'un set dans l'ordre des numéros (voir `number_sort`).
        Index("ix_card_expansion_number_sort", "expansion_id", "number_sort"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    expansion_id: Mapped[int] = mapped_column(
        ForeignKey("expansion.id", ondelete="CASCADE"), nullable=False, index=True
    )

    # Numéro tel qu'imprimé, en texte : "025", "SV49", "TG12", "H1".
    number: Mapped[str] = mapped_column(String(32), nullable=False)
    # Part numérique extraite du numéro, pour trier correctement — sans elle,
    # "10" se classe avant "2". Nulle quand le numéro n'en contient pas.
    number_sort: Mapped[int | None] = mapped_column(Integer)

    rarity_id: Mapped[int | None] = mapped_column(
        ForeignKey("rarity.id", ondelete="SET NULL"), index=True
    )
    illustrator: Mapped[str | None] = mapped_column(String(256))

    # URL de l'illustration. On ne stocke **jamais** l'image elle-même
    # (copyright éditeur) : seulement le lien fourni par la source.
    image_url: Mapped[str | None] = mapped_column(Text)

    # pHash de l'illustration, précalculé pour la reconnaissance photo du
    # lot 7. Laissé nul tant que le lot 7 n'est pas fait.
    image_phash: Mapped[str | None] = mapped_column(String(64), index=True)

    external_ids: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default="{}"
    )

    expansion: Mapped["Expansion"] = relationship(back_populates="cards")
    rarity: Mapped["Rarity | None"] = relationship(back_populates="cards")
    names: Mapped[list["CardName"]] = relationship(
        back_populates="card", cascade="all, delete-orphan"
    )
    printings: Mapped[list["Printing"]] = relationship(
        back_populates="card", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:
        return f"<Card {self.number}>"


class CardName(TimestampMixin, Base):
    """Le nom d'une carte dans une langue donnée.

    C'est cette table qui rend possible « dracaufeu » et « charizard »
    renvoyant la même carte.
    """

    __tablename__ = "card_name"
    __table_args__ = (
        UniqueConstraint("card_id", "language", name="uq_card_name_card_language"),
        # L'index trigram porte sur la forme normalisée, pas sur `name` :
        # sinon une recherche sans accent ne pourrait pas s'en servir.
        Index(
            "ix_card_name_normalized_trgm",
            "name_normalized",
            postgresql_using="gin",
            postgresql_ops={"name_normalized": "gin_trgm_ops"},
        ),
        Index("ix_card_name_language", "language"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    card_id: Mapped[int] = mapped_column(
        ForeignKey("card.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # Code de langue façon BCP 47 : "fr", "en", "ja", "zh-tw".
    # Colonne libre plutôt qu'`ENUM` : on ajoutera des langues sans migration.
    language: Mapped[str] = mapped_column(String(8), nullable=False)
    name: Mapped[str] = mapped_column(String(256), nullable=False)

    # Forme normalisée (minuscules, sans accents), **calculée par PostgreSQL**.
    # Colonne générée et non champ rempli côté Python : la normalisation ne peut
    # alors jamais diverger du nom, quel que soit le chemin d'écriture (import,
    # API, psql à la main). Voir `immutable_unaccent` dans la migration.
    name_normalized: Mapped[str] = mapped_column(
        Text,
        Computed("lower(immutable_unaccent(name))", persisted=True),
        nullable=False,
    )

    card: Mapped["Card"] = relationship(back_populates="names")

    def __repr__(self) -> str:
        return f"<CardName {self.language}:{self.name}>"


class Printing(TimestampMixin, Base):
    """La déclinaison concrète d'une carte : une finition, une langue.

    **C'est le niveau auquel s'attache un prix**, et le niveau qu'on possède.
    """

    __tablename__ = "printing"
    __table_args__ = (
        UniqueConstraint(
            "card_id", "finish_id", "language", name="uq_printing_card_finish_language"
        ),
        Index("ix_printing_external_ids", "external_ids", postgresql_using="gin"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    card_id: Mapped[int] = mapped_column(
        ForeignKey("card.id", ondelete="CASCADE"), nullable=False, index=True
    )
    finish_id: Mapped[int] = mapped_column(
        ForeignKey("finish.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    language: Mapped[str] = mapped_column(String(8), nullable=False, index=True)

    # Correspondance vers la clé de prix de chaque source. pokemontcg.io range
    # par exemple ses relevés Cardmarket sous des clés de variante : c'est ici
    # qu'on notera laquelle correspond à cette impression (lot 5).
    external_ids: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default="{}"
    )

    card: Mapped["Card"] = relationship(back_populates="printings")
    finish: Mapped["Finish"] = relationship(back_populates="printings")
    price_snapshots: Mapped[list["PriceSnapshot"]] = relationship(
        back_populates="printing", cascade="all, delete-orphan"
    )
    collection_items: Mapped[list["CollectionItem"]] = relationship(
        back_populates="printing"
    )
    watches: Mapped[list["WatchedPrinting"]] = relationship(
        back_populates="printing", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:
        return f"<Printing card={self.card_id} finish={self.finish_id} {self.language}>"

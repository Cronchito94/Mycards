"""Référentiel des cartes : extension, carte, localisations, impressions.

Hiérarchie, du plus abstrait au plus concret :

    TCG → EXPANSION ─┬→ EXPANSION_NAME  (un nom par langue)
                     └→ CARD ─┬→ CARD_LOCALIZATION  (ce qui varie par langue)
                              └→ PRINTING  (finition + langue) → prix, exemplaires

Deux séparations portent ce modèle, et elles n'ont pas la même origine.

**CARD / PRINTING** est la décision de départ : « Dracaufeu 4/102 » est *une*
carte, mais sa version normale anglaise et sa reverse holo française sont *deux*
impressions, avec deux cotes distinctes.

**CARD / CARD_LOCALIZATION** est venue des données réelles. En comparant la même
carte en français et en anglais chez TCGdex, seuls `hp`, `retreat`, `dexId`,
`regulationMark` et l'illustrateur sont identiques. Le nom, la rareté, le stade,
les types, les attaques **et l'image** changent avec la langue. Tout ce qui
varie descend donc d'un cran.
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
    from app.models.collection import (
        CollectionItem,
        CollectionSale,
        WatchedPrinting,
    )
    from app.models.pricing import PriceSnapshot
    from app.models.tcg import CardType, Finish, Rarity, Tcg


class Expansion(TimestampMixin, Base):
    """Une extension / un set.

    Aucun nom ici : « Ténèbres Embrasées » et « Darkness Ablaze » sont la même
    extension, et élire l'une des deux comme nom principal aurait figé la langue
    du premier import. Les noms vivent dans `ExpansionName`.
    """

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
    # Code de la source (TCGdex : "swsh3" ; Riftbound : "origins").
    # Clé naturelle d'upsert avec tcg_id, invariante par langue.
    code: Mapped[str] = mapped_column(String(64), nullable=False)
    # Série parente ("Sword & Shield"), utile pour regrouper à l'affichage.
    series: Mapped[str | None] = mapped_column(String(256))
    release_date: Mapped[date | None] = mapped_column(Date)

    # Deux compteurs : le total officiel imprimé sur la carte (le 102 de
    # « 4/102 ») et le nombre réel de cartes du set, secrètes comprises.
    # La complétion du lot 4 a besoin des deux.
    card_count_official: Mapped[int | None] = mapped_column(Integer)
    card_count_total: Mapped[int | None] = mapped_column(Integer)

    # Le symbole est universel chez TCGdex (/univ/), le logo est par langue :
    # on garde ici le seul qui ne varie pas, le logo suit le nom.
    symbol_url: Mapped[str | None] = mapped_column(Text)

    # {"tcgdex": "swsh3"} ou {"apitcg": "origins", "tcgplayerGroup": 24439}
    external_ids: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default="{}"
    )

    tcg: Mapped["Tcg"] = relationship(back_populates="expansions")
    names: Mapped[list["ExpansionName"]] = relationship(
        back_populates="expansion", cascade="all, delete-orphan"
    )
    cards: Mapped[list["Card"]] = relationship(
        back_populates="expansion", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:
        return f"<Expansion {self.code}>"


class ExpansionName(TimestampMixin, Base):
    """Le nom d'une extension dans une langue donnée.

    Table dédiée plutôt que colonne JSONB, contrairement aux vocabulaires de
    `tcg.py` : l'utilisateur cherche et filtre par nom d'extension au lot 3,
    donc il faut un index trigram — ce qu'une clé de JSONB ne permet pas.
    """

    __tablename__ = "expansion_name"
    __table_args__ = (
        UniqueConstraint(
            "expansion_id", "language", name="uq_expansion_name_expansion_language"
        ),
        Index(
            "ix_expansion_name_normalized_trgm",
            "name_normalized",
            postgresql_using="gin",
            postgresql_ops={"name_normalized": "gin_trgm_ops"},
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    expansion_id: Mapped[int] = mapped_column(
        ForeignKey("expansion.id", ondelete="CASCADE"), nullable=False, index=True
    )
    language: Mapped[str] = mapped_column(String(8), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(256), nullable=False)
    name_normalized: Mapped[str] = mapped_column(
        Text,
        Computed("lower(immutable_unaccent(name))", persisted=True),
        nullable=False,
    )
    # Le logo est localisé chez TCGdex (/fr/… vs /en/…), le symbole ne l'est pas.
    logo_url: Mapped[str | None] = mapped_column(Text)

    expansion: Mapped["Expansion"] = relationship(back_populates="names")

    def __repr__(self) -> str:
        return f"<ExpansionName {self.language}:{self.name}>"


class Card(TimestampMixin, Base):
    """La carte au sens abstrait : son numéro dans l'extension, sa rareté, sa
    nature, et ce qui ne dépend ni de la langue ni de la finition.

    Aucun prix ici, jamais : un prix s'attache à une `Printing`.
    Aucun nom ni aucune image non plus : ils dépendent de la langue et vivent
    dans `CardLocalization`.
    """

    __tablename__ = "card"
    __table_args__ = (
        UniqueConstraint("expansion_id", "number", name="uq_card_expansion_number"),
        Index("ix_card_external_ids", "external_ids", postgresql_using="gin"),
        Index("ix_card_attributes", "attributes", postgresql_using="gin"),
        # Tri d'un set dans l'ordre des numéros (voir `number_sort`).
        Index("ix_card_expansion_number_sort", "expansion_id", "number_sort"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    expansion_id: Mapped[int] = mapped_column(
        ForeignKey("expansion.id", ondelete="CASCADE"), nullable=False, index=True
    )

    # Numéro tel qu'imprimé, en texte : "025", "SV49", "TG12", "001/298".
    number: Mapped[str] = mapped_column(String(32), nullable=False)
    # Part numérique extraite du numéro, pour trier correctement — sans elle,
    # "10" se classe avant "2". Nulle quand le numéro n'en contient pas.
    number_sort: Mapped[int | None] = mapped_column(Integer)

    rarity_id: Mapped[int | None] = mapped_column(
        ForeignKey("rarity.id", ondelete="SET NULL"), index=True
    )
    card_type_id: Mapped[int | None] = mapped_column(
        ForeignKey("card_type.id", ondelete="SET NULL"), index=True
    )
    illustrator: Mapped[str | None] = mapped_column(String(256))

    # Caractéristiques de jeu **invariantes par langue**, propres à chaque TCG :
    #   Pokemon   {"hp": 110, "retreat": 1, "dexId": [162],
    #              "regulationMark": "D"}
    #   Riftbound {"energyCost": "5", "powerCost": "0", "might": "5",
    #              "domain": "Fury"}
    #
    # En JSONB et non en colonnes : les deux jeux n'ont aucun attribut commun,
    # et Pokemon en ajoute a chaque mecanique nouvelle. La regle du modele :
    # JSONB pour ce qu'on affiche, table de reference pour ce qu'on interroge
    # (rarete, type de carte). L'index GIN garde la porte ouverte si un
    # attribut devait finalement servir a filtrer.
    attributes: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default="{}"
    )

    external_ids: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default="{}"
    )

    expansion: Mapped["Expansion"] = relationship(back_populates="cards")
    rarity: Mapped["Rarity | None"] = relationship(back_populates="cards")
    card_type: Mapped["CardType | None"] = relationship(back_populates="cards")
    localizations: Mapped[list["CardLocalization"]] = relationship(
        back_populates="card", cascade="all, delete-orphan"
    )
    printings: Mapped[list["Printing"]] = relationship(
        back_populates="card", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:
        return f"<Card {self.number}>"


class CardLocalization(TimestampMixin, Base):
    """Tout ce qu'une carte a de propre à une langue : son nom, son image, son
    texte de jeu.

    C'est cette table qui rend possible « dracaufeu » et « charizard »
    renvoyant la même carte.

    Elle porte aussi l'**image** et son **pHash**, et ce n'est pas un détail :
    une carte française et son équivalent anglais partagent l'illustration mais
    pas l'image imprimée. Photographier une carte française (lot 7) doit la
    comparer aux images françaises, sans quoi le score de confiance s'effondre
    sans cause visible.
    """

    __tablename__ = "card_localization"
    __table_args__ = (
        UniqueConstraint(
            "card_id", "language", name="uq_card_localization_card_language"
        ),
        # L'index trigram porte sur la forme normalisée, pas sur `name` :
        # sinon une recherche sans accent ne pourrait pas s'en servir.
        Index(
            "ix_card_localization_normalized_trgm",
            "name_normalized",
            postgresql_using="gin",
            postgresql_ops={"name_normalized": "gin_trgm_ops"},
        ),
        Index("ix_card_localization_language", "language"),
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
    # API, psql à la main). Voir `immutable_unaccent` dans la migration 0002.
    name_normalized: Mapped[str] = mapped_column(
        Text,
        Computed("lower(immutable_unaccent(name))", persisted=True),
        nullable=False,
    )

    # URL de l'illustration dans cette langue. On ne stocke **jamais** l'image
    # elle-même (copyright éditeur) : seulement le lien fourni par la source.
    image_url: Mapped[str | None] = mapped_column(Text)

    # pHash de l'image, précalculé pour la reconnaissance photo du lot 7.
    # Laissé nul tant que le lot 7 n'est pas fait.
    image_phash: Mapped[str | None] = mapped_column(String(64), index=True)

    # Caractéristiques de jeu **qui changent avec la langue** :
    #   Pokemon   {"stage": "Niveau 1", "types": ["Incolore"],
    #              "evolveFrom": "Fouinette",
    #              "attacks": [{"name": "Mode Cool", "effect": "Piochez 3 cartes."}]}
    #   Riftbound {"description": "ACCELERATE …", "flavorText": null}
    attributes: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default="{}"
    )

    card: Mapped["Card"] = relationship(back_populates="localizations")

    def __repr__(self) -> str:
        return f"<CardLocalization {self.language}:{self.name}>"


class Printing(TimestampMixin, Base):
    """La déclinaison concrète d'une carte : une finition, un format, une langue.

    **C'est le niveau auquel s'attache un prix**, et le niveau qu'on possède.
    """

    __tablename__ = "printing"
    __table_args__ = (
        UniqueConstraint(
            "card_id",
            "finish_id",
            "language",
            "size",
            name="uq_printing_card_finish_language_size",
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

    # Format physique de la carte : "standard" ou "jumbo" chez TCGdex.
    #
    # Colonne à part entière **et membre de la contrainte d'unicité**, parce
    # que le format fait partie de l'identité de l'impression : le Bulbasaur du
    # Set de Base existe en normal standard *et* en normal jumbo, avec deux
    # cotes sans rapport. Sans lui dans la clé, l'import n'avait d'autre choix
    # que de fabriquer des finitions `normal_jumbo` — ce qui multipliait le
    # vocabulaire de `finish` par le nombre de formats et dédoublait
    # l'information. Voir la migration 0006.
    #
    # Non nul et jamais vide : `standard` est la valeur par défaut, pas `NULL`.
    # Un `NULL` sortirait les lignes concernées de la contrainte d'unicité, qui
    # ne les comparerait plus entre elles.
    size: Mapped[str] = mapped_column(
        String(32), nullable=False, server_default="standard"
    )

    # Correspondance vers la clé de prix de chaque source :
    #   {"cardmarket": [483559], "tcgplayer": [219333],
    #    "tcgdexVariantIds": ["cm4kqul3x1bwlz1f"]}
    #
    # Des **listes**, et non des scalaires : 1 762 cartes du corpus portent
    # plusieurs entrées de même finition avec des identifiants Cardmarket
    # différents (relevé du 15/09/2026). Le schéma n'admet qu'une impression
    # par (carte, finition, langue), donc on conserve tous les identifiants
    # plutôt que d'en perdre en silence — le lot 5 tranchera lequel coter.
    #
    # ⚠️ `variantId` n'est PAS un identifiant d'impression malgré son nom :
    # `endfynwn4n10gzq` est porté par 8 914 cartes et la valeur littérale
    # `generated` par 8 861. Il est gardé pour traçabilité, jamais comme clé.
    # Côté Riftbound, chaque carte porte son `tcgplayer.id`.
    external_ids: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default="{}"
    )

    # Ce qui distingue une impression sans être sa finition : le format, par
    # exemple — TCGdex renvoie `size` « Standard » ou « Jumbo ». Rare, mais une
    # jumbo n'a ni la même cote ni la même place dans un classeur.
    attributes: Mapped[dict[str, Any]] = mapped_column(
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
    sales: Mapped[list["CollectionSale"]] = relationship(back_populates="printing")
    watches: Mapped[list["WatchedPrinting"]] = relationship(
        back_populates="printing", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:
        return f"<Printing card={self.card_id} finish={self.finish_id} {self.language}>"

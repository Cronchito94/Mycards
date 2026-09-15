"""Ce que je possède réellement, et ce que je surveille."""

from datetime import date
from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import (
    CheckConstraint,
    Date,
    Enum,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.db.mixins import TimestampMixin
from app.models.enums import CardCondition

if TYPE_CHECKING:
    from app.models.catalog import Printing


class CollectionItem(TimestampMixin, Base):
    """Un exemplaire possédé : une impression, une quantité, un état.

    Plusieurs lignes peuvent viser la même impression : trois exemplaires en NM
    achetés ensemble d'un côté, un exemplaire en GD acheté ailleurs de l'autre.
    Regrouper de force fausserait le prix de revient, donc on ne regroupe pas —
    d'où l'absence de contrainte d'unicité sur `printing_id`.
    """

    __tablename__ = "collection_item"
    __table_args__ = (
        CheckConstraint("quantity > 0", name="ck_collection_item_quantity_positive"),
        CheckConstraint(
            "unit_purchase_price IS NULL OR unit_purchase_price >= 0",
            name="ck_collection_item_price_non_negative",
        ),
        # Prix et devise vont ensemble ou pas du tout : un montant sans devise
        # est ininterprétable, une devise sans montant n'a pas de sens.
        CheckConstraint(
            "(unit_purchase_price IS NULL) = (purchase_currency IS NULL)",
            name="ck_collection_item_price_currency_together",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    # RESTRICT et non CASCADE : un réimport du référentiel qui ferait
    # disparaître une impression ne doit pas effacer ma collection en silence.
    # L'erreur d'intégrité est justement ce qu'on veut voir.
    printing_id: Mapped[int] = mapped_column(
        ForeignKey("printing.id", ondelete="RESTRICT"), nullable=False, index=True
    )

    quantity: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    condition: Mapped[CardCondition] = mapped_column(
        Enum(
            CardCondition,
            name="card_condition",
            values_callable=lambda e: [m.value for m in e],
        ),
        nullable=False,
        default=CardCondition.NEAR_MINT,
    )

    # Prix **unitaire**, pas prix du lot : `quantity=3, unit_purchase_price=60`
    # se lit « trois cartes à 60 € », jamais « trois cartes pour 60 € ». Le nom
    # porte l'information, parce qu'une colonne `purchase_price` obligerait à
    # aller lire la doc — et quelqu'un finirait par se tromper.
    #
    # NUMERIC et jamais float : un prix en binaire flottant finit toujours par
    # produire des totaux faux d'un centime.
    unit_purchase_price: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    purchase_currency: Mapped[str | None] = mapped_column(String(3))
    purchase_date: Mapped[date | None] = mapped_column(Date)

    notes: Mapped[str | None] = mapped_column(Text)

    printing: Mapped["Printing"] = relationship(back_populates="collection_items")

    def __repr__(self) -> str:
        return f"<CollectionItem printing={self.printing_id} x{self.quantity}>"


class SalePlatform(TimestampMixin, Base):
    """Où une carte a été vendue : Cardmarket, Vinted, eBay, en main propre…

    Table de référence et non champ texte libre : sans elle, « Vinted »,
    « vinted » et « VINTED » deviendraient trois plateformes distinctes dans
    les statistiques. Le `code` est normalisé, le `label` garde la saisie.

    À ne pas confondre avec `price_source`, qui dit *où on lit une cote* et
    non *où j'ai vendu*. Les deux se recoupent parfois (Cardmarket est les
    deux) mais ne servent pas à la même chose.
    """

    __tablename__ = "sale_platform"

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    label: Mapped[str] = mapped_column(String(128), nullable=False)

    sales: Mapped[list["CollectionSale"]] = relationship(back_populates="platform")

    def __repr__(self) -> str:
        return f"<SalePlatform {self.code}>"


class CollectionSale(TimestampMixin, Base):
    """Une vente : ce qui est sorti de la collection, et à quel prix.

    Table dédiée plutôt que des colonnes `sold_at` / `sale_price` sur
    `CollectionItem`, pour deux raisons mesurables :

    1. **Les ventes partielles.** Vendre 2 exemplaires sur 4 ne peut pas se
       représenter par une ligne « à moitié vendue ».
    2. **L'historique survit à la collection.** Vendre son dernier exemplaire
       supprime la ligne de collection ; la vente, elle, doit rester. D'où
       `collection_item_id` nullable en `SET NULL` et non en `CASCADE`.

    La vente porte son propre `condition` : un exemplaire peut s'abîmer entre
    l'achat et la revente, et c'est l'état au moment de la vente qui explique
    le prix obtenu.
    """

    __tablename__ = "collection_sale"
    __table_args__ = (
        CheckConstraint("quantity > 0", name="ck_collection_sale_quantity_positive"),
        CheckConstraint(
            "unit_sale_price >= 0", name="ck_collection_sale_price_non_negative"
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    # RESTRICT comme pour la collection : un réimport ne doit pas pouvoir
    # effacer un historique de vente en silence.
    printing_id: Mapped[int] = mapped_column(
        ForeignKey("printing.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    # Le lot d'achat d'origine, quand on le connaît. Nullable : on vend
    # parfois une carte dont le prix d'achat n'a jamais été saisi.
    collection_item_id: Mapped[int | None] = mapped_column(
        ForeignKey("collection_item.id", ondelete="SET NULL"), index=True
    )

    quantity: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    condition: Mapped[CardCondition] = mapped_column(
        Enum(
            CardCondition,
            name="card_condition",
            values_callable=lambda e: [m.value for m in e],
        ),
        nullable=False,
        default=CardCondition.NEAR_MINT,
    )

    # Obligatoires, contrairement au prix d'achat : une vente sans montant
    # n'a aucun sens — c'est un don, qu'on ne modélise pas.
    unit_sale_price: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    sale_currency: Mapped[str] = mapped_column(String(3), nullable=False, default="EUR")
    sale_date: Mapped[date | None] = mapped_column(Date, index=True)

    platform_id: Mapped[int | None] = mapped_column(
        ForeignKey("sale_platform.id", ondelete="SET NULL"), index=True
    )
    notes: Mapped[str | None] = mapped_column(Text)

    printing: Mapped["Printing"] = relationship(back_populates="sales")
    platform: Mapped["SalePlatform | None"] = relationship(back_populates="sales")

    def __repr__(self) -> str:
        return f"<CollectionSale printing={self.printing_id} x{self.quantity}>"


class WatchedPrinting(TimestampMixin, Base):
    """Une impression suivie sans être possédée.

    Le job du lot 5 ne relève les prix que des impressions possédées **ou
    surveillées** — surtout pas de l'intégralité du référentiel. Cette table
    est la seconde moitié de ce filtre ; sans elle, « surveiller » une carte
    avant de l'acheter serait impossible.
    """

    __tablename__ = "watched_printing"
    __table_args__ = (
        UniqueConstraint("printing_id", name="uq_watched_printing_printing"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    printing_id: Mapped[int] = mapped_column(
        ForeignKey("printing.id", ondelete="CASCADE"), nullable=False
    )
    notes: Mapped[str | None] = mapped_column(Text)

    printing: Mapped["Printing"] = relationship(back_populates="watches")

    def __repr__(self) -> str:
        return f"<WatchedPrinting printing={self.printing_id}>"

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
            "purchase_price IS NULL OR purchase_price >= 0",
            name="ck_collection_item_price_non_negative",
        ),
        # Prix et devise vont ensemble ou pas du tout : un montant sans devise
        # est ininterprétable, une devise sans montant n'a pas de sens.
        CheckConstraint(
            "(purchase_price IS NULL) = (purchase_currency IS NULL)",
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

    # NUMERIC et jamais float : un prix en binaire flottant finit toujours par
    # produire des totaux faux d'un centime.
    purchase_price: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    purchase_currency: Mapped[str | None] = mapped_column(String(3))
    purchase_date: Mapped[date | None] = mapped_column(Date)

    notes: Mapped[str | None] = mapped_column(Text)

    printing: Mapped["Printing"] = relationship(back_populates="collection_items")

    def __repr__(self) -> str:
        return f"<CollectionItem printing={self.printing_id} x{self.quantity}>"


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

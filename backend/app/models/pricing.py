"""Relevés de prix : sources et snapshots datés.

Rappel du lot 5 : **aucune API ne fournit d'historique de prix.** Les sources
donnent une photo du jour. L'historique, c'est nous qui le construisons, en
empilant une ligne par jour et par source — d'où cette table de snapshots.
"""

from datetime import date, datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Any

from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.db.mixins import TimestampMixin

if TYPE_CHECKING:
    from app.models.catalog import Printing


class PriceSource(TimestampMixin, Base):
    """Une source de prix : Cardmarket, TCGplayer, …

    Table de référence, pour que le connecteur abstrait du lot 5 puisse
    accueillir une nouvelle source par un simple INSERT.
    """

    __tablename__ = "price_source"

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(32), unique=True, nullable=False)
    label: Mapped[str] = mapped_column(String(128), nullable=False)
    # Devise dans laquelle la source cote habituellement — indicative :
    # la devise qui fait foi est celle stockée sur chaque snapshot.
    default_currency: Mapped[str | None] = mapped_column(String(3))
    homepage_url: Mapped[str | None] = mapped_column(Text)

    snapshots: Mapped[list["PriceSnapshot"]] = relationship(back_populates="source")

    def __repr__(self) -> str:
        return f"<PriceSource {self.code}>"


class PriceSnapshot(Base):
    """Un relevé de prix daté, pour une impression et une source.

    Deux partis pris :

    1. **Une ligne = un point de valorisation.** Les sources renvoient plusieurs
       métriques (tendance, moyenne 7j, plus bas…). On en élit une comme montant
       canonique — `amount`, avec `price_type` qui dit laquelle — et on conserve
       l'intégralité de la réponse dans `raw`. Les séries temporelles du lot 5
       restent ainsi de simples requêtes, sans rien perdre au passage.
    2. **Granularité au jour** (`observed_on`), pas à la seconde : les sources
       se rafraîchissent quotidiennement. La contrainte d'unicité rend le job
       rejouable — le relancer deux fois le même jour met à jour, ne duplique pas.

    Pas de `TimestampMixin` ici : `fetched_at` joue déjà ce rôle, et cette table
    est celle qui grossira le plus — deux colonnes d'horodatage en trop s'y
    paieraient en volume.
    """

    __tablename__ = "price_snapshot"
    __table_args__ = (
        UniqueConstraint(
            "printing_id", "source_id", "observed_on", name="uq_price_snapshot_daily"
        ),
        CheckConstraint("amount >= 0", name="ck_price_snapshot_amount_non_negative"),
        # Index de la série temporelle : « l'historique de cette impression,
        # du plus récent au plus ancien » est *la* requête du lot 5.
        Index(
            "ix_price_snapshot_printing_observed",
            "printing_id",
            "observed_on",
            postgresql_using="btree",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    printing_id: Mapped[int] = mapped_column(
        ForeignKey("printing.id", ondelete="CASCADE"), nullable=False
    )
    source_id: Mapped[int] = mapped_column(
        ForeignKey("price_source.id", ondelete="RESTRICT"), nullable=False, index=True
    )

    # Jour auquel le prix est rattaché (date de cotation de la source).
    observed_on: Mapped[date] = mapped_column(Date, nullable=False)
    # Instant réel de notre appel — permet de distinguer « la source n'a pas
    # bougé » de « on n'a pas relevé ».
    fetched_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    # Métrique retenue comme canonique, ex. "cardmarket.trendPrice",
    # "tcgplayer.market". Indispensable pour ne pas comparer des choux et
    # des carottes entre deux sources.
    price_type: Mapped[str] = mapped_column(String(64), nullable=False)

    # Réponse brute de la source pour ce jour : toutes les autres métriques.
    # Si on change d'avis sur la métrique canonique, l'historique est rejouable.
    raw: Mapped[dict[str, Any] | None] = mapped_column(JSONB)

    printing: Mapped["Printing"] = relationship(back_populates="price_snapshots")
    source: Mapped["PriceSource"] = relationship(back_populates="snapshots")

    def __repr__(self) -> str:
        return f"<PriceSnapshot printing={self.printing_id} {self.observed_on} {self.amount}>"

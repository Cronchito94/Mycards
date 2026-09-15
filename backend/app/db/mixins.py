"""Mixins partagés par les modèles."""

from datetime import datetime

from sqlalchemy import DateTime, func
from sqlalchemy.orm import Mapped, mapped_column


class TimestampMixin:
    """Horodatage de création et de mise à jour.

    Les deux valeurs sont calculées par PostgreSQL (`now()`), pas par Python :
    l'import du lot 2 tournera peut-être depuis une autre machine, et on veut
    une horloge unique — celle de la base.
    """

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

"""Suivi des imports : ce qui a déjà été fait, et jusqu'où.

Les upserts de l'import sont idempotents, donc le relancer ne duplique rien.
Mais sans mémoire, il referait aussi **tout** le trafic HTTP. Cette table note
ce qui est terminé pour qu'une reprise après interruption ne recommence que ce
qui reste.

En base et non dans un fichier : le conteneur est jetable, la base ne l'est pas.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Integer, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.mixins import TimestampMixin


class ImportCheckpoint(TimestampMixin, Base):
    """Une unité d'import terminée : une extension, dans une langue.

    La granularité est l'extension et non la carte : assez fin pour qu'une
    reprise ne coûte que quelques centaines de requêtes, assez grossier pour
    ne pas transformer l'import en écriture permanente.
    """

    __tablename__ = "import_checkpoint"
    __table_args__ = (
        UniqueConstraint(
            "source", "language", "scope_code", name="uq_import_checkpoint_scope"
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    # Source de l'import : "tcgdex", et plus tard "apitcg" pour Riftbound.
    source: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    language: Mapped[str] = mapped_column(String(8), nullable=False)
    # Code de l'extension traitée (TCGdex : "swsh3").
    scope_code: Mapped[str] = mapped_column(String(64), nullable=False)

    cards_seen: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    completed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    def __repr__(self) -> str:
        return f"<ImportCheckpoint {self.source}/{self.language}/{self.scope_code}>"

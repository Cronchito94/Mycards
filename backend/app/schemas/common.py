"""Schémas transverses."""

from __future__ import annotations

from pydantic import BaseModel, Field


class Page[T](BaseModel):
    """Une page de résultats.

    `total` est le nombre total d'éléments correspondant au filtre, pas le
    nombre renvoyé : sans lui le front ne peut pas afficher de pagination.
    """

    items: list[T]
    total: int = Field(description="Nombre total de résultats, toutes pages confondues")
    page: int = Field(ge=1)
    page_size: int = Field(ge=1)

    @property
    def pages(self) -> int:
        return max(1, -(-self.total // self.page_size))

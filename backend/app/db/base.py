"""Base déclarative SQLAlchemy 2.x.

Tous les modèles du lot 1 hériteront de `Base`. Le module est créé dès le
lot 0 pour qu'Alembic ait une cible `target_metadata` stable.
"""

from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """Classe de base de tous les modèles ORM."""

"""Active les extensions PostgreSQL nécessaires

pg_trgm  : index trigram pour la recherche floue sur NOM_LOCALISE (lot 3).
unaccent : normalisation des accents ("dracaufeu" vs "Dracaufeu").

Ces extensions sont posées par une migration plutôt que par un script
d'init Docker : le script d'init ne s'exécute que sur un volume vierge,
alors qu'une migration s'applique aussi sur la base du serveur de déploiement.

Revision ID: 0001
Revises:
Create Date: 2026-01-01 00:00:00

"""
from collections.abc import Sequence

from alembic import op

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
    op.execute("CREATE EXTENSION IF NOT EXISTS unaccent")


def downgrade() -> None:
    op.execute("DROP EXTENSION IF EXISTS unaccent")
    op.execute("DROP EXTENSION IF EXISTS pg_trgm")

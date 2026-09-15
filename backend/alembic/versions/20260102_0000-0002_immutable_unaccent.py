"""Fonction immutable_unaccent pour l'index trigram

`unaccent()` est déclarée STABLE et non IMMUTABLE par PostgreSQL, parce que son
résultat dépend du dictionnaire de recherche plein texte installé. Or une
colonne générée et un index d'expression exigent une fonction IMMUTABLE.

On enveloppe donc `unaccent` en fixant explicitement le dictionnaire — la forme
`unaccent('unaccent'::regdictionary, $1)`, qui ne dépend plus du search_path —
et on déclare le wrapper IMMUTABLE.

Contrepartie assumée, à connaître : si le dictionnaire `unaccent` était un jour
modifié, les valeurs déjà calculées ne seraient pas recalculées et l'index
deviendrait faux. En pratique ce dictionnaire ne bouge pas ; le jour où on y
toucherait, il faudrait un REINDEX et un UPDATE des colonnes générées. C'est le
compromis standard pour indexer une recherche insensible aux accents.

Revision ID: 0002
Revises: 0001
Create Date: 2026-01-02 00:00:00

"""
from collections.abc import Sequence

from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE OR REPLACE FUNCTION immutable_unaccent(text)
        RETURNS text
        LANGUAGE sql
        IMMUTABLE
        STRICT
        PARALLEL SAFE
        AS $$ SELECT unaccent('unaccent'::regdictionary, $1) $$
        """
    )


def downgrade() -> None:
    op.execute("DROP FUNCTION IF EXISTS immutable_unaccent(text)")

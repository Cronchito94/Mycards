"""Le format de la carte entre dans la clé d'unicité de l'impression

L'import du lot 2 fabriquait des finitions `normal_jumbo`, `holo_jumbo`,
`reverse_jumbo`… et rangeait *en plus* la taille dans `printing.attributes`.
Deux sources de vérité pour la même information, et un vocabulaire `finish`
qui double à chaque format rencontré (9 entrées pour 5 finitions réelles).

Ce n'était pas une négligence : la contrainte
`UNIQUE (card_id, finish_id, language)` ne laissait pas le choix. Le Bulbasaur
du Set de Base existe en normal **standard** et en normal **jumbo**, avec deux
cotes sans rapport ; sans le suffixe, les deux impressions entraient en
collision. Le défaut était donc dans le schéma, pas dans l'import.

D'où `printing.size`, non nul, par défaut `standard`, **membre de la contrainte
d'unicité**. Les finitions suffixées sont fusionnées vers leur finition de base
et les impressions concernées reprennent leur taille dans la nouvelle colonne.

Revision ID: 0006
Revises: 0005
Create Date: 2026-09-15 13:30:00

"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = '0006'
down_revision: str | None = '0005'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TAILLE_PAR_DEFAUT = "standard"

# Une finition est « suffixée » quand son code vaut <code de base>_<taille>.
# On ne devine pas la taille : elle est lue dans printing.attributes, que
# l'import remplissait déjà.
SUFFIXEE = (
    "right(f.code, length(p.attributes->>'size') + 1) = '_' || (p.attributes->>'size')"
)
CODE_DE_BASE = "left(f.code, length(f.code) - length(p.attributes->>'size') - 1)"


def upgrade() -> None:
    op.add_column(
        'printing',
        sa.Column('size', sa.String(length=32), nullable=False,
                  server_default=TAILLE_PAR_DEFAUT),
    )

    # La contrainte saute d'abord : le repointage ci-dessous ferait entrer en
    # collision une impression jumbo et sa jumelle standard.
    op.drop_constraint('uq_printing_card_finish_language', 'printing', type_='unique')

    op.execute(
        "UPDATE printing SET size = attributes->>'size' "
        "WHERE attributes ? 'size' AND attributes->>'size' <> ''"
    )

    # Certaines finitions n'existent qu'en version suffixée (`lenticular_jumbo`
    # sans `lenticular`) : il faut créer la base avant de pouvoir y repointer.
    op.execute(
        f"""
        INSERT INTO finish (tcg_id, code, labels, sort_order)
        SELECT DISTINCT f.tcg_id, {CODE_DE_BASE}, f.labels, f.sort_order
        FROM printing p
        JOIN finish f ON f.id = p.finish_id
        WHERE p.attributes ? 'size' AND {SUFFIXEE}
        ON CONFLICT (tcg_id, code) DO NOTHING
        """
    )

    op.execute(
        f"""
        UPDATE printing p
        SET finish_id = base.id
        FROM finish f, finish base
        WHERE f.id = p.finish_id
          AND p.attributes ? 'size'
          AND {SUFFIXEE}
          AND base.tcg_id = f.tcg_id
          AND base.code = {CODE_DE_BASE}
        """
    )

    # Les finitions suffixées n'ont plus d'impression : elles disparaissent.
    # La condition `starts_with` évite de toucher une finition légitime dont le
    # code contiendrait un tiret bas (`first_edition` n'a pas de base `first`).
    op.execute(
        """
        DELETE FROM finish f
        WHERE NOT EXISTS (SELECT 1 FROM printing p WHERE p.finish_id = f.id)
          AND EXISTS (
              SELECT 1 FROM finish base
              WHERE base.tcg_id = f.tcg_id
                AND base.id <> f.id
                AND starts_with(f.code, base.code || '_')
          )
        """
    )

    # La taille ne vit plus qu'à un seul endroit.
    op.execute("UPDATE printing SET attributes = attributes - 'size' "
               "WHERE attributes ? 'size'")

    op.create_unique_constraint(
        'uq_printing_card_finish_language_size',
        'printing',
        ['card_id', 'finish_id', 'language', 'size'],
    )


def downgrade() -> None:
    """Restaure les finitions suffixées.

    Symétrique, mais pas gratuit : les finitions de base créées à l'aller ne
    sont pas détruites au retour. Les distinguer de celles qui existaient déjà
    demanderait de mémoriser un état, pour un gain nul — une finition sans
    impression ne gêne personne et l'import la réutilisera.
    """
    op.drop_constraint(
        'uq_printing_card_finish_language_size', 'printing', type_='unique'
    )

    # Recrée <code>_<taille> pour chaque impression hors format standard.
    op.execute(
        f"""
        INSERT INTO finish (tcg_id, code, labels, sort_order)
        SELECT DISTINCT f.tcg_id, f.code || '_' || p.size, f.labels, f.sort_order
        FROM printing p
        JOIN finish f ON f.id = p.finish_id
        WHERE p.size <> '{TAILLE_PAR_DEFAUT}'
        ON CONFLICT (tcg_id, code) DO NOTHING
        """
    )

    op.execute(
        f"""
        UPDATE printing p
        SET finish_id = suffixee.id,
            attributes = p.attributes || jsonb_build_object('size', p.size)
        FROM finish f, finish suffixee
        WHERE f.id = p.finish_id
          AND p.size <> '{TAILLE_PAR_DEFAUT}'
          AND suffixee.tcg_id = f.tcg_id
          AND suffixee.code = f.code || '_' || p.size
        """
    )

    op.drop_column('printing', 'size')
    op.create_unique_constraint(
        'uq_printing_card_finish_language',
        'printing',
        ['card_id', 'finish_id', 'language'],
    )

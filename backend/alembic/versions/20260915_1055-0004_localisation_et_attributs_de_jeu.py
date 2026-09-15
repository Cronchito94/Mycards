"""Localisation complète, attributs de jeu et types de carte

Fait suite à la confrontation du schéma du lot 1 aux données réelles des deux
jeux du projet (TCGdex pour Pokémon, apitcg pour Riftbound). Trois manques :

1. **Ce qui varie par langue était traité comme invariant.** En comparant la
   même carte en `fr` et en `en` chez TCGdex, le nom, la rareté, le stade, les
   types, les attaques *et l'image* changent ; seuls `hp`, `retreat`, `dexId`,
   `regulationMark` et l'illustrateur sont identiques. `card.image_url`,
   `card.image_phash` et `expansion.name` descendent donc d'un cran.
2. **Aucune place pour les caractéristiques de jeu.** `domain`, `energyCost`,
   `might` côté Riftbound ; `hp`, `retreat`, `types`, `attacks` côté Pokémon.
   D'où `attributes` (JSONB) sur `card`, `card_localization` et `printing`.
3. **Pas de notion de type de carte**, pourtant présente dans les deux jeux et
   destinée à être filtrée au lot 3 — donc une table de référence par TCG, au
   même titre que `rarity` et `finish`, et non une clé de JSONB.

Deux renommages plutôt que des `DROP`/`CREATE` : `card_name` devient
`card_localization`, et `expansion.name` part dans `expansion_name`. Les
données existantes sont transportées, avec la langue `und` (« undetermined »,
ISO 639-2) quand la migration ne peut pas la déduire — au lot 2, l'import
réécrira ces lignes avec la vraie langue.

Écrite à partir d'un `alembic revision --autogenerate`, puis amendée :
l'autogénération proposait de détruire `card_name` et de recréer une table
vide, et rendait un `drop_constraint(None, …)` qui aurait échoué au downgrade.

Revision ID: 0004
Revises: 0003
Create Date: 2026-09-15 10:55:02

"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = '0004'
down_revision: str | None = '0003'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Langue attribuée aux données que la migration ne peut pas rattacher à une
# langue connue (ISO 639-2 : « undetermined »).
LANGUE_INDETERMINEE = "und"


def upgrade() -> None:
    # --- Vocabulaire : le type de carte rejoint rarity et finish -----------
    op.create_table(
        'card_type',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('tcg_id', sa.Integer(), nullable=False),
        sa.Column('code', sa.String(length=64), nullable=False),
        sa.Column('labels', postgresql.JSONB(astext_type=sa.Text()),
                  server_default='{}', nullable=False),
        sa.Column('sort_order', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True),
                  server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True),
                  server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['tcg_id'], ['tcg.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('tcg_id', 'code', name='uq_card_type_tcg_code'),
    )
    op.create_index('ix_card_type_tcg_id', 'card_type', ['tcg_id'], unique=False)

    # --- Les libellés de vocabulaire deviennent multilingues ---------------
    # « Peu Commune » et « Uncommon » sont la même rareté : un libellé unique
    # aurait figé la langue du premier import.
    for table in ('rarity', 'finish'):
        op.add_column(
            table,
            sa.Column('labels', postgresql.JSONB(astext_type=sa.Text()),
                      server_default='{}', nullable=False),
        )
        op.execute(
            f"UPDATE {table} SET labels = jsonb_build_object("
            f"'{LANGUE_INDETERMINEE}', label) WHERE label IS NOT NULL"
        )
        op.drop_column(table, 'label')

    # --- Le nom d'extension descend dans sa propre table -------------------
    op.create_table(
        'expansion_name',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('expansion_id', sa.Integer(), nullable=False),
        sa.Column('language', sa.String(length=8), nullable=False),
        sa.Column('name', sa.String(length=256), nullable=False),
        sa.Column('name_normalized', sa.Text(),
                  sa.Computed('lower(immutable_unaccent(name))', persisted=True),
                  nullable=False),
        sa.Column('logo_url', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True),
                  server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True),
                  server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['expansion_id'], ['expansion.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('expansion_id', 'language',
                            name='uq_expansion_name_expansion_language'),
    )
    op.create_index('ix_expansion_name_expansion_id', 'expansion_name',
                    ['expansion_id'], unique=False)
    op.create_index('ix_expansion_name_language', 'expansion_name',
                    ['language'], unique=False)
    op.create_index('ix_expansion_name_normalized_trgm', 'expansion_name',
                    ['name_normalized'], unique=False,
                    postgresql_using='gin',
                    postgresql_ops={'name_normalized': 'gin_trgm_ops'})

    # Transport des noms existants avant de supprimer la colonne.
    op.execute(
        "INSERT INTO expansion_name (expansion_id, language, name, logo_url) "
        f"SELECT id, '{LANGUE_INDETERMINEE}', name, logo_url FROM expansion"
    )
    op.drop_column('expansion', 'name')
    op.drop_column('expansion', 'logo_url')

    # --- card_name devient card_localization ------------------------------
    # Renommage et non DROP/CREATE : la table peut déjà contenir un import.
    op.rename_table('card_name', 'card_localization')
    op.execute('ALTER TABLE card_localization RENAME CONSTRAINT '
               'uq_card_name_card_language TO uq_card_localization_card_language')
    op.execute('ALTER INDEX ix_card_name_card_id RENAME TO ix_card_localization_card_id')
    op.execute('ALTER INDEX ix_card_name_language RENAME TO ix_card_localization_language')
    op.execute('ALTER INDEX ix_card_name_normalized_trgm '
               'RENAME TO ix_card_localization_normalized_trgm')

    op.add_column('card_localization', sa.Column('image_url', sa.Text(), nullable=True))
    op.add_column('card_localization',
                  sa.Column('image_phash', sa.String(length=64), nullable=True))
    op.add_column(
        'card_localization',
        sa.Column('attributes', postgresql.JSONB(astext_type=sa.Text()),
                  server_default='{}', nullable=False),
    )
    op.create_index('ix_card_localization_image_phash', 'card_localization',
                    ['image_phash'], unique=False)

    # L'image et son empreinte descendent de card vers la localisation.
    # La valeur est recopiée sur toutes les langues de la carte : c'est faux
    # au sens strict (l'image diffère par langue), mais moins destructeur que
    # de la perdre. L'import du lot 2 réécrira chaque ligne avec la bonne URL.
    op.execute(
        "UPDATE card_localization cl "
        "SET image_url = c.image_url, image_phash = c.image_phash "
        "FROM card c WHERE c.id = cl.card_id "
        "AND (c.image_url IS NOT NULL OR c.image_phash IS NOT NULL)"
    )
    op.drop_index('ix_card_image_phash', table_name='card')
    op.drop_column('card', 'image_url')
    op.drop_column('card', 'image_phash')

    # --- Attributs de jeu --------------------------------------------------
    op.add_column('card', sa.Column('card_type_id', sa.Integer(), nullable=True))
    op.create_foreign_key('fk_card_card_type_id', 'card', 'card_type',
                          ['card_type_id'], ['id'], ondelete='SET NULL')
    op.create_index('ix_card_card_type_id', 'card', ['card_type_id'], unique=False)
    for table in ('card', 'printing'):
        op.add_column(
            table,
            sa.Column('attributes', postgresql.JSONB(astext_type=sa.Text()),
                      server_default='{}', nullable=False),
        )
    op.create_index('ix_card_attributes', 'card', ['attributes'],
                    unique=False, postgresql_using='gin')


def downgrade() -> None:
    # Symétrique de `upgrade`, dans l'ordre inverse. Les données transportées
    # reviennent à leur place : le nom d'extension et l'image reprennent une
    # valeur, celle de la langue `und` s'il y en a une, la première sinon.

    # --- Attributs de jeu --------------------------------------------------
    op.drop_index('ix_card_attributes', table_name='card', postgresql_using='gin')
    for table in ('card', 'printing'):
        op.drop_column(table, 'attributes')
    op.drop_index('ix_card_card_type_id', table_name='card')
    op.drop_constraint('fk_card_card_type_id', 'card', type_='foreignkey')
    op.drop_column('card', 'card_type_id')

    # --- L'image remonte de la localisation vers la carte ------------------
    op.add_column('card', sa.Column('image_url', sa.TEXT(),
                                    autoincrement=False, nullable=True))
    op.add_column('card', sa.Column('image_phash', sa.VARCHAR(length=64),
                                    autoincrement=False, nullable=True))
    op.create_index('ix_card_image_phash', 'card', ['image_phash'], unique=False)
    op.execute(
        "UPDATE card c SET image_url = src.image_url, image_phash = src.image_phash "
        "FROM (SELECT DISTINCT ON (card_id) card_id, image_url, image_phash "
        "      FROM card_localization "
        f"     ORDER BY card_id, (language = '{LANGUE_INDETERMINEE}') DESC, id) src "
        "WHERE src.card_id = c.id"
    )

    op.drop_index('ix_card_localization_image_phash', table_name='card_localization')
    op.drop_column('card_localization', 'attributes')
    op.drop_column('card_localization', 'image_phash')
    op.drop_column('card_localization', 'image_url')

    # --- card_localization redevient card_name ----------------------------
    op.execute('ALTER INDEX ix_card_localization_normalized_trgm '
               'RENAME TO ix_card_name_normalized_trgm')
    op.execute('ALTER INDEX ix_card_localization_language '
               'RENAME TO ix_card_name_language')
    op.execute('ALTER INDEX ix_card_localization_card_id RENAME TO ix_card_name_card_id')
    op.execute('ALTER TABLE card_localization RENAME CONSTRAINT '
               'uq_card_localization_card_language TO uq_card_name_card_language')
    op.rename_table('card_localization', 'card_name')

    # --- Le nom d'extension remonte ---------------------------------------
    # `nullable=False` sans valeur par défaut échouerait sur une table peuplée :
    # la colonne est créée permissive, remplie, puis contrainte.
    op.add_column('expansion', sa.Column('logo_url', sa.TEXT(),
                                         autoincrement=False, nullable=True))
    op.add_column('expansion', sa.Column('name', sa.VARCHAR(length=256),
                                         autoincrement=False, nullable=True))
    op.execute(
        "UPDATE expansion e SET name = src.name, logo_url = src.logo_url "
        "FROM (SELECT DISTINCT ON (expansion_id) expansion_id, name, logo_url "
        "      FROM expansion_name "
        f"     ORDER BY expansion_id, (language = '{LANGUE_INDETERMINEE}') DESC, id) src "
        "WHERE src.expansion_id = e.id"
    )
    op.execute("UPDATE expansion SET name = '' WHERE name IS NULL")
    op.alter_column('expansion', 'name', nullable=False)

    op.drop_index('ix_expansion_name_normalized_trgm', table_name='expansion_name',
                  postgresql_using='gin',
                  postgresql_ops={'name_normalized': 'gin_trgm_ops'})
    op.drop_index('ix_expansion_name_language', table_name='expansion_name')
    op.drop_index('ix_expansion_name_expansion_id', table_name='expansion_name')
    op.drop_table('expansion_name')

    # --- Les libellés redeviennent monolingues -----------------------------
    for table in ('rarity', 'finish'):
        op.add_column(table, sa.Column('label', sa.VARCHAR(length=128),
                                       autoincrement=False, nullable=True))
        op.execute(
            f"UPDATE {table} SET label = labels ->> '{LANGUE_INDETERMINEE}' "
            f"WHERE labels ? '{LANGUE_INDETERMINEE}'"
        )
        op.drop_column(table, 'labels')

    # --- Vocabulaire -------------------------------------------------------
    op.drop_index('ix_card_type_tcg_id', table_name='card_type')
    op.drop_table('card_type')

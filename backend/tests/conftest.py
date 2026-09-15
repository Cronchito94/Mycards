"""Outillage commun aux tests.

Porte la garde des tests d'intégration. Elle vivait dans chaque fichier, et
la copie du lot 3 vers le lot 4 a reconduit le même défaut : tester la présence
de `DATABASE_URL` ne prouve rien, la variable étant toujours définie dans le
conteneur. Les tests n'étaient donc jamais sautés et échouaient en masse sur
une base vide — exactement le bruit que la garde devait éviter.

Un seul point de vérité ici, à réutiliser tel quel au lot 5 :

    from tests.conftest import pytestmark_db

    @pytestmark_db
    class TestQuelqueChose: ...
"""

from __future__ import annotations

import pytest


def referentiel_charge() -> bool:
    """Y a-t-il au moins une carte en base ?

    Connexion synchrone volontaire : la garde est évaluée à la collecte, avant
    toute boucle asyncio. Toute erreur — base éteinte, schéma absent, table
    vide — rend False : on saute, on n'échoue pas.
    """
    try:
        import psycopg

        from app.core.config import get_settings

        dsn = get_settings().database_url.replace("+psycopg", "")
        with psycopg.connect(dsn, connect_timeout=3) as connexion:
            ligne = connexion.execute("SELECT EXISTS (SELECT 1 FROM card)").fetchone()
            return bool(ligne and ligne[0])
    except Exception:
        return False


pytestmark_db = pytest.mark.skipif(
    not referentiel_charge(),
    reason=(
        "référentiel non importé : docker compose exec api "
        "python -m app.importers.cli --languages en,fr"
    ),
)

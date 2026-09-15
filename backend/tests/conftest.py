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


@pytest.fixture
async def client():
    """Client HTTP sur l'application, sans serveur réseau."""
    import httpx

    from app.main import app

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


class BacASable:
    """Annule exactement ce que le test a fait, ni plus ni moins.

    Le piège : un ajout **sans prix fusionne** avec une ligne existante. Une
    suppression brutale emporterait donc les exemplaires qu'on n'a pas créés —
    sur la vraie collection, c'est une perte de données. D'où la distinction
    entre une ligne créée (à supprimer) et une ligne rejointe (à ramener à sa
    quantité d'avant).
    """

    def __init__(self, client) -> None:
        self._client = client
        self._creees: list[int] = []
        # (id de ligne, quantité qu'elle avait avant qu'on y touche)
        self._rejointes: list[tuple[int, int]] = []
        self.sales: list[int] = []

    async def ajouter(self, **payload):
        """POST /collection/items en retenant de quoi l'annuler exactement."""
        reponse = await self._client.post("/api/v1/collection/items", json=payload)
        if reponse.status_code == 201:
            self._creees.append(reponse.json()["id"])
        elif reponse.status_code == 200:
            # 200 = la ligne existait. La réponse porte la quantité APRÈS
            # fusion ; l'écart avec ce qu'on a ajouté donne celle d'avant.
            apres = reponse.json()["quantity"]
            self._rejointes.append(
                (reponse.json()["id"], apres - payload.get("quantity", 1))
            )
        return reponse

    @property
    def items(self) -> list[int]:
        """Lignes créées par le test, supprimables sans risque."""
        return self._creees

    async def nettoyer(self) -> None:
        for sale_id in self.sales:
            await self._client.delete(f"/api/v1/collection/sales/{sale_id}")
        for item_id, avant in self._rejointes:
            if avant > 0:
                await self._client.patch(
                    f"/api/v1/collection/items/{item_id}", json={"quantity": avant}
                )
            else:
                await self._client.delete(f"/api/v1/collection/items/{item_id}")
        for item_id in self._creees:
            await self._client.delete(f"/api/v1/collection/items/{item_id}")


@pytest.fixture
async def bac_a_sable(client):
    """Suit ce que le test crée et l'annule ensuite, quoi qu'il arrive.

    Jamais de TRUNCATE : la base de développement portera un jour la vraie
    collection, et un test distrait l'effacerait.
    """
    bac = BacASable(client)
    yield bac
    await bac.nettoyer()

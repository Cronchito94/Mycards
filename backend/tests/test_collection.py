"""Tests de la collection.

Ces tests écrivent dans la base de développement, qui contient un jour la
**vraie** collection. Ils ne vident donc jamais rien : chaque test crée ce dont
il a besoin, raisonne en **écarts** plutôt qu'en valeurs absolues, et nettoie
ses propres lignes. Un test qui fait `TRUNCATE` finirait par effacer une vraie
collection un jour de distraction.
"""

from __future__ import annotations

import pytest

from tests.conftest import pytestmark_db


async def _une_impression(client, terme: str = "dracaufeu") -> int:
    """Une impression réelle du référentiel, jamais un identifiant inventé."""
    recherche = (
        await client.get(f"/api/v1/cards?q={terme}&expansion=base1&page_size=1")
    ).json()
    assert recherche["items"], "référentiel non importé : lancer l'import du lot 2"
    detail = (
        await client.get(f"/api/v1/cards/{recherche['items'][0]['id']}")
    ).json()
    return detail["printings"][0]["id"]


@pytestmark_db
@pytest.mark.asyncio
class TestAjout:
    async def test_ajout_simple_fusionne(self, client, bac_a_sable) -> None:
        """Le geste courant : ajouter sans prix incrémente la ligne existante."""
        pid = await _une_impression(client)

        premier = await bac_a_sable.ajouter(printing_id=pid, quantity=1)
        item_id = premier.json()["id"]
        depart = premier.json()["quantity"]

        second = await bac_a_sable.ajouter(printing_id=pid, quantity=2)
        # 200 et non 201 : rien n'a été créé, une ligne a grossi.
        assert second.status_code == 200
        assert second.json()["id"] == item_id
        # En écart, pas en absolu : la collection peut déjà contenir cette carte.
        assert second.json()["quantity"] == depart + 2

    async def test_ajout_avec_prix_cree_un_lot(self, client, bac_a_sable) -> None:
        """Un prix d'achat est un prix de revient : il ne se fusionne pas."""
        pid = await _une_impression(client)

        sans_prix = await bac_a_sable.ajouter(printing_id=pid, quantity=1)

        avec_prix = await client.post(
            "/api/v1/collection/items",
            json={
                "printing_id": pid,
                "quantity": 1,
                "unit_purchase_price": "180.00",
                "purchase_currency": "EUR",
            },
        )
        assert avec_prix.status_code == 201
        assert avec_prix.json()["id"] != sans_prix.json()["id"]
        bac_a_sable.items.append(avec_prix.json()["id"])

    async def test_prix_sans_devise_refuse(self, client) -> None:
        pid = await _une_impression(client)
        r = await client.post(
            "/api/v1/collection/items",
            json={"printing_id": pid, "unit_purchase_price": "10.00"},
        )
        assert r.status_code == 409

    async def test_impression_inexistante_refusee(self, client) -> None:
        r = await client.post(
            "/api/v1/collection/items", json={"printing_id": 999_999_999}
        )
        assert r.status_code == 409


@pytestmark_db
@pytest.mark.asyncio
class TestGroupement:
    async def test_groupe_par_impression_jamais_par_nom(
        self, client, bac_a_sable
    ) -> None:
        """Deux Pikachu d'extensions différentes sont deux entrées distinctes.

        C'est la garantie centrale du lot : « Pikachu » désigne des dizaines de
        cartes, et les confondre rendrait la collection illisible.
        """
        res = (
            await client.get("/api/v1/cards?q=pikachu&language=fr&page_size=60")
        ).json()
        extensions, cartes = set(), []
        for item in res["items"]:
            code = item["expansion"]["code"]
            if item["names"].get("fr") == "Pikachu" and code not in extensions:
                extensions.add(code)
                cartes.append(item)
            if len(cartes) == 2:
                break
        assert len(cartes) == 2, "pas assez de Pikachu distincts dans le référentiel"

        impressions, attendus = [], {}
        for carte, quantite in zip(cartes, (3, 1), strict=True):
            detail = (await client.get(f"/api/v1/cards/{carte['id']}")).json()
            pid = detail["printings"][0]["id"]
            impressions.append(pid)
            r = await bac_a_sable.ajouter(printing_id=pid, quantity=quantite)
            attendus[pid] = r.json()["quantity"]

        listing = (
            await client.get("/api/v1/collection/items?q=pikachu&page_size=50")
        ).json()
        par_impression = {
            e["printing"]["id"]: e["quantity"] for e in listing["items"]
        }
        # Deux entrées distinctes, chacune à sa quantité : jamais une seule
        # entrée « Pikachu ×4 » qui confondrait deux cartes différentes.
        assert impressions[0] != impressions[1]
        for pid, attendu in attendus.items():
            assert par_impression[pid] == attendu

    async def test_entree_porte_une_miniature(self, client, bac_a_sable) -> None:
        """Le front du lot 6 doit pouvoir afficher une image, pas une ligne."""
        pid = await _une_impression(client)
        r = await client.post("/api/v1/collection/items", json={"printing_id": pid})
        bac_a_sable.items.append(r.json()["id"])

        listing = (
            await client.get("/api/v1/collection/items?q=dracaufeu&page_size=10")
        ).json()
        entree = next(e for e in listing["items"] if e["printing"]["id"] == pid)
        carte = entree["printing"]["card"]
        assert carte["image_url"], "aucune miniature exposée"
        # L'image doit être celle de la langue possédée.
        assert f"/{entree['printing']['language']}/" in carte["image_url"]


@pytestmark_db
@pytest.mark.asyncio
class TestVentes:
    async def test_vente_partielle_decremente(self, client, bac_a_sable) -> None:
        pid = await _une_impression(client)
        r = await bac_a_sable.ajouter(printing_id=pid, quantity=5)
        avant = r.json()["quantity"]

        vente = await client.post(
            "/api/v1/collection/sales",
            json={"printing_id": pid, "quantity": 2, "unit_sale_price": "12.50"},
        )
        assert vente.status_code == 201
        bac_a_sable.sales.append(vente.json()["id"])

        listing = (
            await client.get("/api/v1/collection/items?q=dracaufeu&page_size=20")
        ).json()
        entree = next(e for e in listing["items"] if e["printing"]["id"] == pid)
        # Deux exemplaires de moins qu'avant la vente.
        assert entree["quantity"] == avant - 2

    async def test_vendre_plus_que_possede_refuse(self, client, bac_a_sable) -> None:
        pid = await _une_impression(client)
        r = await client.post(
            "/api/v1/collection/items", json={"printing_id": pid, "quantity": 1}
        )
        bac_a_sable.items.append(r.json()["id"])

        vente = await client.post(
            "/api/v1/collection/sales",
            json={"printing_id": pid, "quantity": 999, "unit_sale_price": "5"},
        )
        assert vente.status_code == 409

    async def test_vendre_le_dernier_exemplaire_garde_l_historique(
        self, client, bac_a_sable
    ) -> None:
        """Vendre son dernier exemplaire supprime la ligne de collection ; la
        vente, elle, doit survivre — sinon le total des ventes serait faux."""
        pid = await _une_impression(client)
        r = await client.post(
            "/api/v1/collection/items", json={"printing_id": pid, "quantity": 1}
        )
        item_id = r.json()["id"]

        vente = await client.post(
            "/api/v1/collection/sales",
            json={"printing_id": pid, "quantity": 1, "unit_sale_price": "10"},
        )
        assert vente.status_code == 201
        bac_a_sable.sales.append(vente.json()["id"])

        # La ligne de collection a disparu…
        assert (await client.delete(f"/api/v1/collection/items/{item_id}")).status_code == 409
        # …mais la vente est toujours là.
        ventes = (await client.get("/api/v1/collection/sales?page_size=100")).json()
        assert any(v["id"] == vente.json()["id"] for v in ventes["items"])

    async def test_plateforme_normalisee(self, client, bac_a_sable) -> None:
        """« VINTED » et « vinted » sont une seule plateforme, pas deux."""
        pid = await _une_impression(client)
        r = await client.post(
            "/api/v1/collection/items", json={"printing_id": pid, "quantity": 2}
        )
        bac_a_sable.items.append(r.json()["id"])

        libelles = set()
        for plateforme in ("VINTED", "vinted"):
            v = await client.post(
                "/api/v1/collection/sales",
                json={
                    "printing_id": pid,
                    "quantity": 1,
                    "unit_sale_price": "8",
                    "platform": plateforme,
                },
            )
            bac_a_sable.sales.append(v.json()["id"])
            libelles.add(v.json()["platform"])
        assert len(libelles) == 1


@pytestmark_db
@pytest.mark.asyncio
class TestStatistiques:
    async def test_trois_compteurs_distincts(self, client, bac_a_sable) -> None:
        """« Combien de cartes ai-je ? » a trois réponses.

        Trois exemplaires d'une même impression, c'est 3 exemplaires, mais
        1 impression et 1 carte.
        """
        avant = (await client.get("/api/v1/collection/stats")).json()
        pid = await _une_impression(client)
        r = await client.post(
            "/api/v1/collection/items", json={"printing_id": pid, "quantity": 3}
        )
        bac_a_sable.items.append(r.json()["id"])
        apres = (await client.get("/api/v1/collection/stats")).json()

        assert apres["items"] - avant["items"] == 3
        assert apres["distinct_printings"] - avant["distinct_printings"] <= 1
        assert apres["distinct_cards"] - avant["distinct_cards"] <= 1

    async def test_devises_jamais_additionnees(self, client, bac_a_sable) -> None:
        """Deux devises donnent deux totaux, jamais une somme convertie."""
        pid = await _une_impression(client)
        for prix, devise in (("10.00", "EUR"), ("12.00", "USD")):
            r = await client.post(
                "/api/v1/collection/items",
                json={
                    "printing_id": pid,
                    "quantity": 1,
                    "unit_purchase_price": prix,
                    "purchase_currency": devise,
                },
            )
            bac_a_sable.items.append(r.json()["id"])

        stats = (await client.get("/api/v1/collection/stats")).json()
        devises = {t["currency"] for t in stats["purchase_value"]}
        assert {"EUR", "USD"} <= devises

    async def test_completion_deux_denominateurs(self, client, bac_a_sable) -> None:
        pid = await _une_impression(client)
        r = await client.post("/api/v1/collection/items", json={"printing_id": pid})
        bac_a_sable.items.append(r.json()["id"])

        lignes = (
            await client.get("/api/v1/collection/completion?tcg=pokemon")
        ).json()
        base1 = next(x for x in lignes if x["expansion"]["code"] == "base1")
        assert base1["owned"] >= 1
        # Les deux dénominateurs sont rendus : le set officiel et le set complet.
        assert base1["official"] is not None
        assert base1["total"] is not None

    async def test_completion_filtree_par_langue(self, client, bac_a_sable) -> None:
        """Une carte anglaise ne compte pas dans un set français."""
        recherche = (
            await client.get("/api/v1/cards?q=dracaufeu&expansion=base1&page_size=1")
        ).json()
        detail = (
            await client.get(f"/api/v1/cards/{recherche['items'][0]['id']}")
        ).json()
        impression_en = next(p for p in detail["printings"] if p["language"] == "en")

        r = await client.post(
            "/api/v1/collection/items", json={"printing_id": impression_en["id"]}
        )
        bac_a_sable.items.append(r.json()["id"])

        en = (
            await client.get("/api/v1/collection/completion?language=en")
        ).json()
        assert any(x["expansion"]["code"] == "base1" for x in en)

"""Tests de la recherche.

Deux niveaux : ce qui se teste sans rien (échappement, filtres) et ce qui
exige le référentiel chargé. Les seconds se **sautent** proprement quand le
référentiel n'est pas importé, plutôt que d'échouer et de masquer un vrai
problème.

La garde interroge les **données**, pas la configuration. Tester la présence
de `DATABASE_URL` ne marchait pas : la variable est toujours définie dans le
conteneur, donc les tests ne sautaient jamais et sortaient cinq échecs sur une
base vide — exactement le bruit que la garde devait éviter.
"""

from __future__ import annotations

import dataclasses

import pytest

from app.services.search import CardFilters, escape_like


class TestEscapeLike:
    """Un joker saisi par l'utilisateur ne doit jamais atteindre le motif SQL."""

    def test_pourcent(self) -> None:
        assert escape_like("100%") == "100\\%"

    def test_underscore(self) -> None:
        # Sans échappement, « dra_aufeu » matcherait « dracaufeu » par joker.
        assert escape_like("dra_aufeu") == "dra\\_aufeu"

    def test_antislash_echappe_en_premier(self) -> None:
        # Si l'antislash passait en dernier, il ré-échapperait les échappements
        # ajoutés juste avant et le motif serait faux.
        assert escape_like("a\\%b") == "a\\\\\\%b"

    def test_terme_ordinaire_inchange(self) -> None:
        assert escape_like("dracaufeu") == "dracaufeu"


class TestCardFilters:
    def test_defaut_sans_filtre(self) -> None:
        f = CardFilters()
        assert (f.tcg, f.expansion, f.rarity, f.card_type, f.language) == (
            None, None, None, None, None,
        )

    def test_immuable(self) -> None:
        # Gelé : un filtre qui muterait en cours de requête donnerait un
        # comptage et une page incohérents entre eux.
        f = CardFilters(tcg="pokemon")
        with pytest.raises(dataclasses.FrozenInstanceError):
            f.tcg = "autre"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Tests d'intégration : exigent la base et le référentiel du lot 2.
# ---------------------------------------------------------------------------

def _referentiel_charge() -> bool:
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
    not _referentiel_charge(),
    reason=(
        "référentiel non importé : docker compose exec api "
        "python -m app.importers.cli --languages en,fr"
    ),
)


@pytest.fixture
async def client():
    import httpx

    from app.main import app

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytestmark_db
@pytest.mark.asyncio
class TestSearchApi:
    async def test_le_critere_de_fin(self, client) -> None:
        """« dracaufeu » et « charizard » doivent donner les mêmes cartes.

        La comparaison porte sur les correspondances **exactes** : le flou
        ramène en plus les voisins de chaque langue, qui diffèrent par nature.
        """

        async def exacts(terme: str) -> set[int]:
            trouves: set[int] = set()
            page = 1
            while True:
                r = await client.get(
                    f"/api/v1/cards?q={terme}&page_size=100&page={page}"
                )
                data = r.json()
                trouves |= {i["id"] for i in data["items"] if i["score"] == 1.0}
                if page * 100 >= data["total"]:
                    return trouves
                page += 1

        fr, en = await exacts("dracaufeu"), await exacts("charizard")
        assert fr, "aucune carte trouvée : le référentiel est-il importé ?"
        # Toute carte nommée Dracaufeu est nommée Charizard en anglais.
        assert fr <= en

    async def test_insensible_aux_accents_et_a_la_casse(self, client) -> None:
        sans = (await client.get("/api/v1/cards?q=evoli&page_size=5")).json()
        avec = (await client.get("/api/v1/cards?q=%C3%A9voli&page_size=5")).json()
        majuscules = (await client.get("/api/v1/cards?q=EVOLI&page_size=5")).json()
        assert sans["total"] == avec["total"] == majuscules["total"]
        assert sans["total"] > 0

    async def test_tolerance_aux_fautes(self, client) -> None:
        r = (await client.get("/api/v1/cards?q=dracofeu&page_size=50")).json()
        noms = {n for i in r["items"] for n in i["names"].values()}
        assert any("Dracaufeu" in n for n in noms)

    async def test_joker_like_neutralise(self, client) -> None:
        # Si `_` passait comme joker, on retrouverait Dracaufeu en exact (1.0).
        r = (await client.get("/api/v1/cards?q=dra_aufeu&page_size=5")).json()
        assert all(i["score"] < 1.0 for i in r["items"])

    async def test_terme_trop_court_refuse(self, client) -> None:
        r = await client.get("/api/v1/cards?q=a")
        assert r.status_code == 422

    async def test_pagination_sans_chevauchement(self, client) -> None:
        vus: set[int] = set()
        for page in range(1, 4):
            r = (
                await client.get(f"/api/v1/cards?q=pikachu&page={page}&page_size=25")
            ).json()
            ids = {i["id"] for i in r["items"]}
            assert not (vus & ids), "une carte apparaît sur deux pages"
            vus |= ids

    async def test_detail_expose_toutes_les_impressions(self, client) -> None:
        recherche = (
            await client.get("/api/v1/cards?q=dracaufeu&expansion=base1&page_size=1")
        ).json()
        card_id = recherche["items"][0]["id"]
        detail = (await client.get(f"/api/v1/cards/{card_id}")).json()
        assert detail["printings"], "une carte sans impression n'est pas exploitable"
        assert {p["language"] for p in detail["printings"]} >= {"en", "fr"}
        assert detail["names"]["fr"] == "Dracaufeu"
        assert detail["names"]["en"] == "Charizard"

    async def test_carte_inexistante(self, client) -> None:
        r = await client.get("/api/v1/cards/999999999")
        assert r.status_code == 404

    async def test_filtre_sur_le_code_pas_le_libelle(self, client) -> None:
        par_code = (await client.get("/api/v1/cards?rarity=uncommon&page_size=1")).json()
        par_libelle = (
            await client.get("/api/v1/cards?rarity=Peu%20Commune&page_size=1")
        ).json()
        assert par_code["total"] > 0
        assert par_libelle["total"] == 0

"""Tests du relevé de prix et de la valorisation.

Le mapping des finitions est testé **sans base ni réseau** : c'est le point
délicat du lot, et il vaut mieux qu'il soit vérifiable seul.

Les tests de valorisation, eux, ont besoin de la base. Ils insèrent leurs
propres relevés et les retirent ensuite — jamais de `TRUNCATE`, la base de
développement portera un jour la vraie collection.

Les montants utilisés sont ceux **réellement relevés** sur `swsh3-136` le
15/09/2026 (trend 0,09 € en normal, trend-holo 0,19 € en reverse), pas des
valeurs inventées.
"""

from __future__ import annotations

import asyncio
from datetime import date
from decimal import Decimal

import pytest

from app.pricing.base import PrintingRef
from app.pricing.tcgdex import TcgdexPriceProvider
from tests.conftest import pytestmark_db

# Extrait réel de /v2/en/cards/swsh3-136, tronqué aux champs utilisés.
CHARGE_REELLE = {
    "id": "swsh3-136",
    "variants_detailed": [
        {
            "type": "normal",
            "size": "standard",
            "thirdParty": {"cardmarket": 483559, "tcgplayer": 219333},
            "pricing": {
                "cardmarket": {
                    "updated": "2026-09-15T14:49:26.292Z",
                    "unit": "EUR",
                    "idProduct": 483559,
                    "avg": 0.08, "low": 0.02, "trend": 0.09,
                    "avg1": 0.06, "avg7": 0.1, "avg30": 0.09,
                    "avg-holo": 0.26, "low-holo": 0.04, "trend-holo": 0.19,
                }
            },
        },
        {
            "type": "reverse",
            "size": "standard",
            "thirdParty": {"cardmarket": 483559, "tcgplayer": 219333},
            # Bloc IDENTIQUE à celui de la variante normale : c'est le piège.
            "pricing": {
                "cardmarket": {
                    "updated": "2026-09-15T14:49:26.292Z",
                    "unit": "EUR",
                    "idProduct": 483559,
                    "avg": 0.08, "low": 0.02, "trend": 0.09,
                    "avg1": 0.06, "avg7": 0.1, "avg30": 0.09,
                    "avg-holo": 0.26, "low-holo": 0.04, "trend-holo": 0.19,
                }
            },
        },
    ],
}


def _ref(printing_id: int, finish: str, size: str = "standard") -> PrintingRef:
    return PrintingRef(
        printing_id=printing_id,
        external_ids={},
        card_external_ids={"tcgdex": "swsh3-136"},
        finish_code=finish,
        size=size,
        language="fr",
    )


class TestMappingFinitions:
    """Cardmarket ne sépare pas ses cotes par variante : la reverse se lit
    dans les champs suffixés `-holo` du même bloc."""

    @staticmethod
    def _extraire(finish: str):
        provider = TcgdexPriceProvider("http://inutilise")
        try:
            return provider._extraire(_ref(1, finish), CHARGE_REELLE)
        finally:
            asyncio.get_event_loop_policy().new_event_loop().close()

    def test_normal_lit_trend(self) -> None:
        cote = self._extraire("normal")
        assert cote is not None
        assert cote.amount == Decimal("0.09")
        assert cote.price_type == "cardmarket.trend"
        assert cote.currency == "EUR"

    def test_reverse_lit_trend_holo(self) -> None:
        """Sans cette règle, toutes les reverse seraient sous-évaluées de
        moitié — 0,09 € au lieu de 0,19 €."""
        cote = self._extraire("reverse")
        assert cote is not None
        assert cote.amount == Decimal("0.19")
        assert cote.price_type == "cardmarket.trend-holo"

    def test_date_de_cotation_lue_dans_la_source(self) -> None:
        cote = self._extraire("normal")
        assert cote is not None
        assert cote.observed_on == date(2026, 9, 15)

    def test_bloc_complet_conserve(self) -> None:
        """`raw` garde tout : changer de métrique ne demandera pas de
        réinterroger la source."""
        cote = self._extraire("normal")
        assert cote is not None
        assert cote.raw is not None
        assert cote.raw["avg30"] == 0.09

    def test_montant_jamais_par_un_float(self) -> None:
        """`Decimal(str(0.1))` vaut exactement 0.1, `Decimal(0.1)` non."""
        cote = self._extraire("normal")
        assert cote is not None
        assert cote.amount == Decimal("0.09")
        assert str(cote.amount) == "0.09"

    def test_sans_cote_renvoie_rien(self) -> None:
        provider = TcgdexPriceProvider("http://inutilise")
        assert provider._extraire(_ref(1, "normal"), {"id": "x"}) is None


@pytestmark_db
@pytest.mark.asyncio
class TestValorisation:
    async def test_valeur_de_marche_et_serie(self, client, bac_a_sable) -> None:
        """Une carte sans prix d'achat prend sa cote de marché.

        C'est la demande d'origine : « si on n'ajoute pas de prix
        manuellement, la carte ne vaut pas 0 mais prend la cote Cardmarket, et
        si on en a plusieurs ça augmente la valeur ».
        """
        recherche = (
            await client.get("/api/v1/cards?q=furret&expansion=swsh3&page_size=1")
        ).json()
        detail = (
            await client.get(f"/api/v1/cards/{recherche['items'][0]['id']}")
        ).json()
        par_finition = {
            (p["finish"]["code"], p["language"]): p["id"] for p in detail["printings"]
        }
        p_normal = par_finition[("normal", "fr")]

        avant = (await client.get("/api/v1/collection/valuation")).json()
        base = sum(
            Decimal(t["total"]) for t in avant["totals"] if t["currency"] == "EUR"
        )

        # Trois exemplaires, aucun prix d'achat saisi.
        r = await client.post(
            "/api/v1/collection/items",
            json={"printing_id": p_normal, "quantity": 3},
        )
        bac_a_sable.items.append(r.json()["id"])

        apres = (await client.get("/api/v1/collection/valuation")).json()
        total = sum(
            Decimal(t["total"]) for t in apres["totals"] if t["currency"] == "EUR"
        )
        sans_cote = apres["uncovered_quantity"]

        # Soit la carte a une cote et le total monte, soit elle n'en a pas et
        # elle est comptée à part. Jamais valorisée à zéro en silence.
        assert total > base or sans_cote >= 3

    async def test_serie_temporelle_repond(self, client) -> None:
        r = await client.get("/api/v1/collection/valuation/history?days=7")
        assert r.status_code == 200
        assert isinstance(r.json(), list)

    async def test_devises_jamais_additionnees(self, client) -> None:
        """Chaque devise a sa ligne : il n'existe aucun total global."""
        v = (await client.get("/api/v1/collection/valuation")).json()
        devises = [t["currency"] for t in v["totals"]]
        assert len(devises) == len(set(devises))

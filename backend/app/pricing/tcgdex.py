"""Connecteur de prix adossé au serveur TCGdex auto-hébergé.

Le serveur embarque ses propres connecteurs Cardmarket et TCGplayer et expose
leurs cotes sur chaque carte. Vérifié le 15/09/2026 sur un réseau sans
inspection TLS — extrait réel de `swsh3-136` :

    "variants_detailed": [
      {"type": "normal", "size": "standard",
       "thirdParty": {"cardmarket": 483559, "tcgplayer": 219333},
       "pricing": {
         "cardmarket": {"updated": "2026-09-15T14:49:26.292Z", "unit": "EUR",
                        "trend": 0.09, "avg1": 0.06, "avg7": 0.1, "avg30": 0.09,
                        "trend-holo": 0.19, "avg1-holo": 0.35, …},
         "tcgplayer": {"unit": "USD",
                       "normal": {"marketPrice": 0.21, …},
                       "reverse-holofoil": {"marketPrice": 0.46, …}}}}]

**Le piège, mesuré et non supposé.** Les entrées `normal` et `reverse` d'une
même carte portent un bloc `cardmarket` **identique**, avec le même
`idProduct` : Cardmarket ne sépare pas ses cotes par variante. La reverse se
lit dans les champs suffixés `-holo` du même bloc. Appliquer `trend` aux deux
sous-évaluerait toutes les reverse de moitié (0,09 € au lieu de 0,19 €).

TCGplayer, lui, sépare proprement par clé de variante (`normal`,
`reverse-holofoil`). Deux sources, deux logiques.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

import httpx

from app.pricing.base import PriceProvider, PriceQuote, PrintingRef, ProviderReport

logger = logging.getLogger(__name__)

# Métrique canonique, choisie avec l'utilisateur le 15/09/2026 : `trend` est
# l'estimation lissée de Cardmarket, celle que les collectionneurs regardent.
# `avg1` serait plus proche de « dernière vente » mais saute d'un jour à
# l'autre. Tout le bloc est conservé dans `raw` : changer d'avis ne demandera
# pas de réinterroger la source.
METRIQUE_CARDMARKET = "trend"

# Suffixe des champs Cardmarket décrivant la version reverse holo.
SUFFIXE_REVERSE = "-holo"

# Finitions pour lesquelles Cardmarket expose une cote distincte via le suffixe.
FINITIONS_REVERSE = frozenset({"reverse"})


class TcgdexPriceProvider(PriceProvider):
    """Lit les cotes Cardmarket servies par le serveur TCGdex."""

    code = "cardmarket"
    label = "Cardmarket (via TCGdex)"
    default_currency = "EUR"
    homepage_url = "https://www.cardmarket.com"

    def __init__(
        self,
        base_url: str,
        *,
        language: str = "en",
        concurrency: int = 8,
        timeout: float = 30.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        # La langue n'a aucune incidence sur les prix — le bloc `pricing` est
        # rattaché au produit, pas à la localisation. On en fixe une pour
        # éviter de multiplier les appels par le nombre de langues possédées.
        self.language = language
        self._client = httpx.AsyncClient(
            base_url=self.base_url,
            timeout=timeout,
            limits=httpx.Limits(max_connections=concurrency + 2),
            headers={"User-Agent": "mycards-pricing/0.1"},
        )
        self._cache: dict[str, dict[str, Any] | None] = {}

    async def close(self) -> None:
        await self._client.aclose()

    async def fetch(self, printings: Sequence[PrintingRef]) -> ProviderReport:
        rapport = ProviderReport()
        # Plusieurs impressions partagent une carte (finitions, langues) : on
        # ne télécharge la carte qu'une fois.
        for impression in printings:
            card_id = impression.card_external_ids.get("tcgdex")
            if not card_id:
                rapport.uncovered.append(
                    (impression.printing_id, "aucun identifiant TCGdex sur la carte")
                )
                continue

            charge = await self._card(str(card_id), rapport)
            if charge is None:
                rapport.uncovered.append(
                    (impression.printing_id, f"carte {card_id} introuvable")
                )
                continue

            cote = self._extraire(impression, charge)
            if cote is None:
                rapport.uncovered.append(
                    (impression.printing_id, "aucune cote Cardmarket pour cette variante")
                )
                continue
            rapport.quotes.append(cote)

        return rapport

    async def _card(
        self, card_id: str, rapport: ProviderReport
    ) -> dict[str, Any] | None:
        if card_id in self._cache:
            return self._cache[card_id]
        try:
            reponse = await self._client.get(f"/v2/{self.language}/cards/{card_id}")
            charge = reponse.json() if reponse.status_code == 200 else None
        except Exception as exc:  # noqa: BLE001 — une carte en échec ne doit pas
            # emporter tout le relevé ; elle remonte au rapport.
            rapport.errors.append((card_id, str(exc)))
            charge = None
        self._cache[card_id] = charge
        return charge

    def _extraire(
        self, impression: PrintingRef, charge: dict[str, Any]
    ) -> PriceQuote | None:
        bloc = self._bloc_cardmarket(impression, charge)
        if not bloc:
            return None

        # Le champ à lire dépend de la finition : voir l'en-tête du module.
        champ = METRIQUE_CARDMARKET
        if impression.finish_code in FINITIONS_REVERSE:
            champ = f"{METRIQUE_CARDMARKET}{SUFFIXE_REVERSE}"

        montant = _to_decimal(bloc.get(champ))
        if montant is None:
            return None

        return PriceQuote(
            printing_id=impression.printing_id,
            source_code=self.code,
            amount=montant,
            currency=str(bloc.get("unit") or self.default_currency),
            price_type=f"cardmarket.{champ}",
            observed_on=_to_date(bloc.get("updated")),
            raw=bloc,
        )

    def _bloc_cardmarket(
        self, impression: PrintingRef, charge: dict[str, Any]
    ) -> dict[str, Any] | None:
        """Retrouve le bloc de cotes correspondant à cette impression.

        On cherche d'abord la variante exacte (type + format) ; à défaut on
        retombe sur le bloc de la carte, identique en pratique puisque
        Cardmarket ne distingue pas les variantes.
        """
        for entree in charge.get("variants_detailed") or []:
            type_norm = str(entree.get("type") or "").strip().lower()
            taille_norm = str(entree.get("size") or "standard").strip().lower()
            if type_norm == impression.finish_code and taille_norm == impression.size:
                bloc = (entree.get("pricing") or {}).get("cardmarket")
                if bloc:
                    return bloc
        return (charge.get("pricing") or {}).get("cardmarket")


def _to_decimal(valeur: Any) -> Decimal | None:
    """Convertit sans jamais passer par un float intermédiaire.

    `Decimal(str(0.1))` vaut exactement 0.1 ; `Decimal(0.1)` non. Sur des
    milliers de cartes, l'écart finirait par se voir dans le total.
    """
    if valeur is None:
        return None
    try:
        montant = Decimal(str(valeur))
    except (InvalidOperation, ValueError):
        return None
    return montant if montant >= 0 else None


def _to_date(valeur: Any) -> date:
    """Date de cotation de la source, à défaut aujourd'hui."""
    if isinstance(valeur, str):
        try:
            return datetime.fromisoformat(valeur.replace("Z", "+00:00")).date()
        except ValueError:
            pass
    return date.today()

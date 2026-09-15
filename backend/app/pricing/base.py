"""Interface abstraite des connecteurs de prix.

La spec l'exige, et l'histoire du projet lui donne raison : `docs/SPEC.md`
désignait pokemontcg.io comme source, qui s'est dégradée entre-temps ;
Cardmarket a fermé son API aux nouvelles demandes. Une source de prix est
une pièce **remplaçable**, pas un socle.

Un connecteur ne fait qu'une chose : pour un lot d'impressions, renvoyer les
cotes qu'il connaît. Il ne décide pas ce qu'on relève, n'écrit rien en base,
et ne choisit pas la métrique canonique — tout cela appartient au job.
"""

from __future__ import annotations

import abc
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Any


@dataclass(frozen=True)
class PrintingRef:
    """Ce qu'un connecteur doit savoir d'une impression pour la coter.

    Volontairement détaché de l'ORM : un connecteur se teste sans base.
    """

    printing_id: int
    # Identifiants externes de l'impression et de sa carte, fusionnés.
    external_ids: dict[str, Any]
    card_external_ids: dict[str, Any]
    finish_code: str
    size: str
    language: str


@dataclass(frozen=True)
class PriceQuote:
    """Une cote relevée, prête à devenir un `price_snapshot`.

    `amount` est le montant **canonique** retenu, `price_type` dit lequel c'est
    (`cardmarket.trend`, `tcgplayer.marketPrice`…), et `raw` conserve la
    réponse complète de la source. Si on change d'avis sur la métrique, on
    rejoue l'historique depuis `raw` sans réinterroger la source.
    """

    printing_id: int
    source_code: str
    amount: Decimal
    currency: str
    price_type: str
    observed_on: date
    raw: dict[str, Any] | None = None


@dataclass
class ProviderReport:
    """Ce que le connecteur a vu, y compris ce qu'il n'a pas su coter."""

    quotes: list[PriceQuote] = field(default_factory=list)
    # Impressions traitées sans qu'aucune cote n'en sorte, avec la raison.
    # Elles **doivent** remonter : une valorisation silencieusement partielle
    # est pire qu'une valorisation manquante.
    uncovered: list[tuple[int, str]] = field(default_factory=list)
    errors: list[tuple[str, str]] = field(default_factory=list)


class PriceProvider(abc.ABC):
    """Contrat commun à toutes les sources de prix."""

    #: Code de la source, tel qu'il sera enregistré dans `price_source`.
    code: str
    #: Libellé lisible.
    label: str
    #: Devise habituelle — indicative : celle qui fait foi est sur le relevé.
    default_currency: str | None = None
    homepage_url: str | None = None

    @abc.abstractmethod
    async def fetch(self, printings: Sequence[PrintingRef]) -> ProviderReport:
        """Relève les cotes connues pour ces impressions."""

    async def close(self) -> None:
        """Libère ce qui doit l'être.

        Volontairement concrète et vide : la plupart des connecteurs n'ont rien
        à fermer, et les forcer à écrire un `pass` n'apporterait rien.
        """
        return None

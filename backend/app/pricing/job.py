"""Job de relevé des prix.

Règle non négociable de la spec : on ne relève **que les impressions possédées
ou surveillées**, jamais l'intégralité du référentiel. 64 301 impressions
contre quelques centaines réellement concernées — la différence n'est pas une
optimisation, c'est ce qui rend le job tenable et poli envers la source.

L'historique, lui, est **notre** construction : aucune source ne le fournit.
D'où un relevé par jour, empilé dans `price_snapshot`.
"""

from __future__ import annotations

import logging
import time
from collections import Counter
from dataclasses import dataclass, field

from sqlalchemy import select, union
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    Card,
    CollectionItem,
    Expansion,
    Finish,
    PriceSnapshot,
    PriceSource,
    Printing,
    Tcg,
    WatchedPrinting,
)
from app.pricing.base import PriceProvider, PrintingRef

logger = logging.getLogger(__name__)


@dataclass
class PriceJobReport:
    """Ce que le relevé a fait, et surtout ce qu'il n'a pas pu faire."""

    source: str = ""
    targeted: int = 0
    quotes: int = 0
    written: int = 0
    uncovered: list[tuple[int, str]] = field(default_factory=list)
    errors: list[tuple[str, str]] = field(default_factory=list)
    started_at: float = field(default_factory=time.monotonic)

    @property
    def duration_s(self) -> float:
        return time.monotonic() - self.started_at

    def render(self) -> str:
        lignes = [
            "",
            "=" * 68,
            f"  RELEVÉ DE PRIX — source : {self.source}",
            "=" * 68,
            f"  {'impressions visées':<24} {self.targeted:>7}"
            "   (possédées ou surveillées)",
            f"  {'cotes obtenues':<24} {self.quotes:>7}",
            f"  {'relevés écrits':<24} {self.written:>7}",
            f"  {'sans cote':<24} {len(self.uncovered):>7}",
            f"  {'durée':<24} {self.duration_s:>7.1f} s",
        ]
        if self.uncovered:
            motifs = Counter(motif for _, motif in self.uncovered)
            lignes += ["", "  SANS COTE, par motif :"]
            lignes += [f"    - {motif} : {n}" for motif, n in motifs.most_common()]
        if self.errors:
            lignes += ["", f"  ERREURS ({len(self.errors)}) :"]
            lignes += [f"    - {ctx} : {msg}" for ctx, msg in self.errors[:10]]
        else:
            lignes += ["", "  Aucune erreur."]
        if self.targeted and not self.quotes:
            lignes += [
                "",
                "  ⚠️  AUCUNE COTE OBTENUE.",
                "     Le serveur TCGdex charge-t-il bien les prix ?",
                "     Avec CI=true il n'en charge aucun : voir docs/PRICING.md.",
            ]
        lignes.append("=" * 68)
        return "\n".join(lignes)


async def targeted_printings(
    session: AsyncSession, *, tcg: str | None = None
) -> list[PrintingRef]:
    """Les impressions à relever : possédées **ou** surveillées.

    L'union des deux est exactement ce que `WatchedPrinting` rendait possible —
    sans elle, surveiller une carte avant de l'acheter serait impossible.
    """
    possedees = select(CollectionItem.printing_id.label("printing_id"))
    surveillees = select(WatchedPrinting.printing_id.label("printing_id"))
    interessantes = union(possedees, surveillees).subquery()

    stmt = (
        select(
            Printing.id,
            Printing.external_ids,
            Printing.language,
            Printing.size,
            Finish.code,
            Card.external_ids.label("card_external_ids"),
        )
        .join(Finish, Finish.id == Printing.finish_id)
        .join(Card, Card.id == Printing.card_id)
        .where(Printing.id.in_(select(interessantes.c.printing_id)))
    )
    if tcg:
        stmt = (
            stmt.join(Expansion, Expansion.id == Card.expansion_id)
            .join(Tcg, Tcg.id == Expansion.tcg_id)
            .where(Tcg.code == tcg)
        )

    return [
        PrintingRef(
            printing_id=ligne.id,
            external_ids=ligne.external_ids or {},
            card_external_ids=ligne.card_external_ids or {},
            finish_code=ligne.code,
            size=ligne.size,
            language=ligne.language,
        )
        for ligne in (await session.execute(stmt)).all()
    ]


async def _ensure_source(session: AsyncSession, provider: PriceProvider) -> int:
    stmt = (
        insert(PriceSource)
        .values(
            code=provider.code,
            label=provider.label,
            default_currency=provider.default_currency,
            homepage_url=provider.homepage_url,
        )
        .on_conflict_do_update(
            index_elements=["code"],
            set_={"label": provider.label, "homepage_url": provider.homepage_url},
        )
        .returning(PriceSource.id)
    )
    return (await session.execute(stmt)).scalar_one()


async def run_price_job(
    session: AsyncSession,
    provider: PriceProvider,
    *,
    tcg: str | None = None,
    dry_run: bool = False,
) -> PriceJobReport:
    """Relève les cotes et les empile dans `price_snapshot`.

    `dry_run` interroge la source et rend le rapport **sans rien écrire** :
    c'est ce qui permet de valider le mapping des finitions sur de vraies
    cartes avant d'alimenter l'historique.
    """
    rapport = PriceJobReport(source=provider.code)

    cibles = await targeted_printings(session, tcg=tcg)
    rapport.targeted = len(cibles)
    if not cibles:
        return rapport

    resultat = await provider.fetch(cibles)
    rapport.quotes = len(resultat.quotes)
    rapport.uncovered = resultat.uncovered
    rapport.errors = resultat.errors

    if dry_run or not resultat.quotes:
        return rapport

    source_id = await _ensure_source(session, provider)
    for cote in resultat.quotes:
        stmt = (
            insert(PriceSnapshot)
            .values(
                printing_id=cote.printing_id,
                source_id=source_id,
                observed_on=cote.observed_on,
                amount=cote.amount,
                currency=cote.currency,
                price_type=cote.price_type,
                raw=cote.raw,
            )
            # Rejouable : relancer le job le même jour met à jour, ne duplique
            # pas. C'est la contrainte d'unicité du lot 1 qui le garantit.
            .on_conflict_do_update(
                index_elements=["printing_id", "source_id", "observed_on"],
                set_={
                    "amount": cote.amount,
                    "currency": cote.currency,
                    "price_type": cote.price_type,
                    "raw": cote.raw,
                },
            )
        )
        await session.execute(stmt)
        rapport.written += 1

    await session.commit()
    return rapport

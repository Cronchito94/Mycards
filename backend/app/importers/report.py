"""Rapport de fin d'import.

Volontairement passif : l'importeur y dépose des compteurs et des anomalies,
il ne décide de rien. Ce qui n'a pas pu être importé doit **apparaître**, pas
être comblé — une carte absente est une information, pas un trou à boucher.
"""

from __future__ import annotations

import time
from collections import Counter
from dataclasses import dataclass, field


@dataclass
class ImportReport:
    """Compteurs et anomalies d'une exécution d'import."""

    source: str
    languages: list[str] = field(default_factory=list)
    started_at: float = field(default_factory=time.monotonic)

    expansions: Counter[str] = field(default_factory=Counter)
    cards: Counter[str] = field(default_factory=Counter)
    localizations: Counter[str] = field(default_factory=Counter)
    printings: Counter[str] = field(default_factory=Counter)
    vocabularies: Counter[str] = field(default_factory=Counter)

    skipped_expansions: int = 0
    http_retries: int = 0
    # (contexte, message) — jamais tronqué silencieusement : on garde tout.
    errors: list[tuple[str, str]] = field(default_factory=list)
    warnings: list[tuple[str, str]] = field(default_factory=list)

    def error(self, contexte: str, message: str) -> None:
        self.errors.append((contexte, message))

    def warn(self, contexte: str, message: str) -> None:
        self.warnings.append((contexte, message))

    @property
    def duration_s(self) -> float:
        return time.monotonic() - self.started_at

    def render(self) -> str:
        """Rapport lisible en fin d'exécution."""

        def bloc(titre: str, c: Counter[str]) -> str:
            if not c:
                return f"  {titre:<16} —"
            detail = "  ".join(f"{k}={v}" for k, v in sorted(c.items()))
            return f"  {titre:<16} {sum(c.values()):>7}   ({detail})"

        lignes = [
            "",
            "=" * 68,
            f"  IMPORT {self.source.upper()} — langues : {', '.join(self.languages)}",
            "=" * 68,
            bloc("extensions", self.expansions),
            bloc("cartes", self.cards),
            bloc("localisations", self.localizations),
            bloc("impressions", self.printings),
            bloc("vocabulaires", self.vocabularies),
            f"  {'extensions sautées':<16} {self.skipped_expansions:>7}"
            "   (déjà importées, voir --force)",
            f"  {'reprises HTTP':<16} {self.http_retries:>7}",
            f"  {'durée':<16} {self.duration_s:>7.1f} s",
        ]

        if self.warnings:
            lignes += ["", f"  AVERTISSEMENTS ({len(self.warnings)}) :"]
            lignes += [f"    - {ctx} : {msg}" for ctx, msg in self.warnings[:20]]
            if len(self.warnings) > 20:
                lignes.append(f"    … {len(self.warnings) - 20} autres")

        if self.errors:
            lignes += ["", f"  ERREURS ({len(self.errors)}) :"]
            lignes += [f"    - {ctx} : {msg}" for ctx, msg in self.errors[:20]]
            if len(self.errors) > 20:
                lignes.append(f"    … {len(self.errors) - 20} autres")
        else:
            lignes += ["", "  Aucune erreur."]

        lignes.append("=" * 68)
        return "\n".join(lignes)

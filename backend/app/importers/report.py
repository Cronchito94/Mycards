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

    # Compteurs d'**opérations**, ventilés par langue : une carte vue en `en`
    # puis en `fr` compte deux fois. Ce ne sont pas des volumes de base — voir
    # `totals`, qui les rapporte aux lignes réellement présentes.
    expansions: Counter[str] = field(default_factory=Counter)
    cards: Counter[str] = field(default_factory=Counter)
    localizations: Counter[str] = field(default_factory=Counter)
    printings: Counter[str] = field(default_factory=Counter)
    vocabularies: Counter[str] = field(default_factory=Counter)

    # Nombre de lignes en base à la fin de l'import, par table. Rempli par
    # l'importeur : sans lui, le rapport annonçait 45 528 cartes là où la base
    # en contenait 23 649, et toute vérification partait de travers.
    totals: dict[str, int] = field(default_factory=dict)

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

        largeur = 22

        def bloc(titre: str, c: Counter[str]) -> str:
            if not c:
                return f"  {titre:<{largeur}} —"
            detail = "  ".join(f"{k}={v}" for k, v in sorted(c.items()))
            return f"  {titre:<{largeur}} {sum(c.values()):>7}   ({detail})"

        lignes = [
            "",
            "=" * 68,
            f"  IMPORT {self.source.upper()} — langues : {', '.join(self.languages)}",
            "=" * 68,
            "  Opérations (une carte vue dans deux langues compte deux fois)",
            bloc("extensions traitées", self.expansions),
            bloc("cartes traitées", self.cards),
            bloc("localisations écrites", self.localizations),
            bloc("impressions écrites", self.printings),
            bloc("vocabulaires", self.vocabularies),
            f"  {'extensions sautées':<{largeur}} {self.skipped_expansions:>7}"
            "   (déjà importées, voir --force)",
            f"  {'reprises HTTP':<{largeur}} {self.http_retries:>7}",
            f"  {'durée':<{largeur}} {self.duration_s:>7.1f} s",
        ]

        # Les volumes réels : c'est sur eux qu'on vérifie un import, et ils ne
        # coïncident pas avec les compteurs ci-dessus.
        if self.totals:
            lignes += ["", "  En base"]
            lignes += [
                f"  {table:<{largeur}} {nombre:>7}"
                for table, nombre in sorted(self.totals.items())
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

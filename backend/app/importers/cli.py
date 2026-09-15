"""Ligne de commande de l'import du référentiel.

    docker compose exec api python -m app.importers.cli --languages en,fr

La première langue de `--languages` fait foi pour tout ce qui est invariant
(numéro, rareté, finitions) ; les suivantes n'apportent que des localisations.
Mettre `fr` en premier figerait les codes de rareté en français.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys

from app.core.config import get_settings
from app.db.session import SessionLocal, engine
from app.importers.report import ImportReport
from app.importers.tcgdex.client import TcgdexClient
from app.importers.tcgdex.importer import SOURCE, TcgdexImporter

logger = logging.getLogger("import")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="app.importers.cli",
        description="Importe le référentiel Pokémon depuis TCGdex (idempotent).",
    )
    p.add_argument(
        "--languages",
        default="en,fr",
        help="Langues à importer, séparées par des virgules. La PREMIÈRE fait "
        "foi pour les données invariantes (défaut : en,fr).",
    )
    p.add_argument(
        "--force",
        action="store_true",
        help="Réimporte les extensions déjà marquées terminées.",
    )
    p.add_argument(
        "--expansions",
        default=None,
        help="Restreint à ces codes d'extension, séparés par des virgules "
        "(ex. swsh3,base1). Utile pour un essai rapide.",
    )
    p.add_argument("--base-url", default=None, help="Surcharge TCGDEX_BASE_URL.")
    p.add_argument(
        "--rate-limit",
        type=float,
        default=None,
        help="Requêtes/seconde (0 = illimité). Obligatoire contre l'API publique.",
    )
    p.add_argument("--concurrency", type=int, default=None)
    p.add_argument("--verbose", "-v", action="store_true")
    return p


async def run(args: argparse.Namespace) -> int:
    settings = get_settings()
    langues = [x.strip() for x in args.languages.split(",") if x.strip()]
    if not langues:
        print("Aucune langue demandée.", file=sys.stderr)
        return 2

    base_url = args.base_url or settings.tcgdex_base_url
    rate = settings.tcgdex_rate_limit if args.rate_limit is None else args.rate_limit
    conc = args.concurrency or settings.tcgdex_concurrency
    filtre = (
        {x.strip() for x in args.expansions.split(",") if x.strip()}
        if args.expansions
        else None
    )

    report = ImportReport(source=SOURCE)
    logger.info("Source : %s (débit %s req/s, concurrence %s)", base_url, rate or "illimité", conc)

    client = TcgdexClient(
        base_url,
        concurrency=conc,
        rate_limit=rate,
        on_retry=lambda: report.__setattr__("http_retries", report.http_retries + 1),
    )
    try:
        async with SessionLocal() as session:
            importer = TcgdexImporter(
                session,
                client,
                report,
                languages=langues,
                force=args.force,
                expansion_filter=filtre,
            )
            await importer.run()
    finally:
        await client.close()
        await engine.dispose()

    print(report.render())
    # Un import partiel n'est pas un succès : le code de sortie le dit, pour
    # qu'un ordonnanceur puisse s'en apercevoir.
    return 1 if report.errors else 0


def main() -> int:
    args = build_parser().parse_args()
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-5s %(message)s",
        datefmt="%H:%M:%S",
    )
    # httpx journalise chaque requête en INFO : 45 000 lignes noieraient le
    # rapport. On ne garde que ses avertissements.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    return asyncio.run(run(args))


if __name__ == "__main__":
    raise SystemExit(main())

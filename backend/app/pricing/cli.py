"""Ligne de commande du relevé de prix.

    docker compose exec api python -m app.pricing.cli --dry-run
    docker compose exec api python -m app.pricing.cli

`--dry-run` interroge la source et affiche le rapport **sans rien écrire** :
à utiliser pour valider le mapping des finitions avant d'alimenter
l'historique.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys

from app.core.config import get_settings
from app.db.session import SessionLocal, engine
from app.pricing.job import run_price_job
from app.pricing.tcgdex import TcgdexPriceProvider

logger = logging.getLogger("prix")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="app.pricing.cli",
        description="Relève les cotes des impressions possédées ou surveillées.",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Interroge la source et affiche le rapport sans rien écrire.",
    )
    p.add_argument("--tcg", default=None, help="Restreint à un jeu, ex. pokemon.")
    p.add_argument("--base-url", default=None, help="Surcharge TCGDEX_BASE_URL.")
    p.add_argument("--verbose", "-v", action="store_true")
    return p


async def run(args: argparse.Namespace) -> int:
    settings = get_settings()
    provider = TcgdexPriceProvider(args.base_url or settings.tcgdex_base_url)
    try:
        async with SessionLocal() as session:
            rapport = await run_price_job(
                session, provider, tcg=args.tcg, dry_run=args.dry_run
            )
    finally:
        await provider.close()
        await engine.dispose()

    if args.dry_run:
        print("  (simulation : rien n'a été écrit en base)", file=sys.stderr)
    print(rapport.render())
    # Un relevé qui ne ramène rien alors qu'il visait des impressions n'est
    # pas un succès : le code de sortie le dit, pour qu'un ordonnanceur le voie.
    if rapport.errors or (rapport.targeted and not rapport.quotes):
        return 1
    return 0


def main() -> int:
    args = build_parser().parse_args()
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-5s %(message)s",
        datefmt="%H:%M:%S",
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)
    return asyncio.run(run(args))


if __name__ == "__main__":
    raise SystemExit(main())

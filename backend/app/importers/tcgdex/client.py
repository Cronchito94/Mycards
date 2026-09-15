"""Client HTTP TCGdex : limitation de débit, reprises, concurrence bornée.

Le débit par défaut est calibré pour l'**API publique**, pas pour l'instance
auto-hébergée. Contre un serveur local, `rate_limit=0` désactive la limitation
et l'import passe de plusieurs dizaines de minutes à environ deux.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

import httpx

logger = logging.getLogger(__name__)


class RateLimiter:
    """Seau à jetons : au plus `rate` requêtes par seconde, en moyenne.

    `rate <= 0` désactive la limitation (cas de l'instance locale).
    """

    def __init__(self, rate: float) -> None:
        self._rate = rate
        self._interval = 1.0 / rate if rate > 0 else 0.0
        self._next = 0.0
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        if self._interval <= 0:
            return
        async with self._lock:
            maintenant = time.monotonic()
            attente = self._next - maintenant
            if attente > 0:
                await asyncio.sleep(attente)
                maintenant = time.monotonic()
            self._next = maintenant + self._interval


class TcgdexClient:
    """Accès aux endpoints TCGdex utiles à l'import.

    L'API ne pagine pas : `/v2/{lang}/sets` et `/v2/{lang}/cards` renvoient la
    liste complète en une fois (vérifié : 218 extensions, 23 548 cartes en EN).
    Le volume vient du **détail par carte**, qu'on parallélise sous sémaphore
    plutôt que de paginer.
    """

    def __init__(
        self,
        base_url: str,
        *,
        concurrency: int = 16,
        rate_limit: float = 0.0,
        timeout: float = 30.0,
        max_retries: int = 4,
        on_retry: Any = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self._sem = asyncio.Semaphore(concurrency)
        self._limiter = RateLimiter(rate_limit)
        self._max_retries = max_retries
        self._on_retry = on_retry
        self._client = httpx.AsyncClient(
            base_url=self.base_url,
            timeout=timeout,
            limits=httpx.Limits(max_connections=concurrency + 4),
            headers={"User-Agent": "mycards-importer/0.1 (+usage personnel)"},
            follow_redirects=True,
        )

    async def __aenter__(self) -> TcgdexClient:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.close()

    async def close(self) -> None:
        await self._client.aclose()

    async def _get(self, path: str) -> Any:
        """GET avec reprises exponentielles.

        On ne réessaie que ce qui a une chance d'aboutir : erreurs réseau et
        5xx. Un 404 est une réponse, pas une panne — il remonte tel quel pour
        que l'appelant décide (une carte absente doit se voir dans le rapport).
        """
        delai = 1.0
        derniere: Exception | None = None

        for tentative in range(self._max_retries + 1):
            async with self._sem:
                await self._limiter.acquire()
                try:
                    reponse = await self._client.get(path)
                except (httpx.TransportError, httpx.TimeoutException) as exc:
                    derniere = exc
                else:
                    if reponse.status_code == 404:
                        raise NotFoundError(path)
                    if reponse.status_code < 500 and reponse.status_code != 429:
                        reponse.raise_for_status()
                        return reponse.json()
                    derniere = httpx.HTTPStatusError(
                        f"HTTP {reponse.status_code}",
                        request=reponse.request,
                        response=reponse,
                    )

            if tentative < self._max_retries:
                if self._on_retry:
                    self._on_retry()
                logger.debug("Reprise %s sur %s (%s)", tentative + 1, path, derniere)
                await asyncio.sleep(delai)
                delai *= 2

        raise RuntimeError(f"{path} : échec après {self._max_retries} reprises") from derniere

    async def list_sets(self, language: str) -> list[dict[str, Any]]:
        return await self._get(f"/v2/{language}/sets")

    async def get_set(self, language: str, set_id: str) -> dict[str, Any]:
        return await self._get(f"/v2/{language}/sets/{set_id}")

    async def get_card(self, language: str, card_id: str) -> dict[str, Any]:
        return await self._get(f"/v2/{language}/cards/{card_id}")


class NotFoundError(Exception):
    """La ressource n'existe pas dans cette langue. Ce n'est pas une panne."""

    def __init__(self, path: str) -> None:
        super().__init__(f"404 sur {path}")
        self.path = path

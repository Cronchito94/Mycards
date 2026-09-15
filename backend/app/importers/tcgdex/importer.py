"""Import idempotent du référentiel TCGdex.

Trois principes, dans cet ordre d'importance :

**1. Une langue de référence porte les données invariantes.**
Une carte a un seul numéro, une seule rareté, une seule liste de finitions —
mais TCGdex les renvoie traduits. Si le premier import était français, le code
de rareté serait `peu_commune` et l'interface serait figée en français. La
première langue de la liste (l'anglais par défaut) fait donc foi pour tout ce
qui est invariant ; les suivantes n'apportent que des localisations et leurs
libellés. Voir `--languages`.

**2. Tout est un upsert.** `INSERT … ON CONFLICT DO UPDATE` sur les clés
naturelles. Relancer l'import ne duplique rien, et corrige ce qui a changé
en amont.

**3. Ce qui manque se voit.** Une carte absente d'une langue, un type de
finition inconnu, une extension en erreur : tout remonte au rapport. Rien
n'est comblé.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.importers.report import ImportReport
from app.importers.tcgdex.client import NotFoundError, TcgdexClient
from app.importers.tcgdex.mapping import (
    extract_finishes,
    normalize_code,
    number_sort,
    split_attributes,
)
from app.models import (
    Card,
    CardLocalization,
    CardType,
    Expansion,
    ExpansionName,
    Finish,
    ImportCheckpoint,
    Printing,
    Rarity,
    Tcg,
)

logger = logging.getLogger(__name__)

SOURCE = "tcgdex"
TCG_CODE = "pokemon"
TCG_NAME = "Pokémon"


class TcgdexImporter:
    """Orchestre l'import d'un référentiel TCGdex vers notre schéma."""

    def __init__(
        self,
        session: AsyncSession,
        client: TcgdexClient,
        report: ImportReport,
        *,
        languages: list[str],
        force: bool = False,
        expansion_filter: set[str] | None = None,
    ) -> None:
        self.session = session
        self.client = client
        self.report = report
        self.languages = languages
        # La première langue fait foi pour l'invariant (principe 1).
        self.reference_language = languages[0]
        self.force = force
        self.expansion_filter = expansion_filter

        self._tcg_id: int | None = None
        # Caches : évitent un aller-retour SQL par carte sur des tables courtes.
        self._rarity_cache: dict[str, int] = {}
        self._card_type_cache: dict[str, int] = {}
        self._finish_cache: dict[str, int] = {}

    # ------------------------------------------------------------------
    # Vocabulaires (rarity / card_type / finish)
    # ------------------------------------------------------------------

    async def _upsert_vocabulary(
        self, model: Any, cache: dict[str, int], code: str, label: str | None, language: str
    ) -> int:
        """Crée ou complète une entrée de vocabulaire.

        Les libellés **fusionnent** par langue (`labels || EXCLUDED.labels`) :
        chaque passe de langue enrichit la même ligne, au lieu de l'écraser.
        Le `code`, lui, ne bouge jamais — c'est la clé d'upsert.
        """
        labels = {language: label} if label else {}
        stmt = (
            insert(model)
            .values(tcg_id=self._tcg_id, code=code, labels=labels)
            .on_conflict_do_update(
                index_elements=["tcg_id", "code"],
                set_={"labels": model.labels.op("||")(insert(model).excluded.labels)},
            )
            .returning(model.id)
        )
        identifiant = (await self.session.execute(stmt)).scalar_one()
        if code not in cache:
            self.report.vocabularies[model.__tablename__] += 1
        cache[code] = identifiant
        return identifiant

    async def _rarity_id(self, brut: str | None, language: str) -> int | None:
        code = normalize_code(brut)
        if not code:
            return None
        return await self._upsert_vocabulary(Rarity, self._rarity_cache, code, brut, language)

    async def _card_type_id(self, brut: str | None, language: str) -> int | None:
        code = normalize_code(brut)
        if not code:
            return None
        return await self._upsert_vocabulary(
            CardType, self._card_type_cache, code, brut, language
        )

    async def _finish_id(self, code: str, label: str | None, language: str) -> int:
        return await self._upsert_vocabulary(Finish, self._finish_cache, code, label, language)

    # ------------------------------------------------------------------
    # TCG
    # ------------------------------------------------------------------

    async def _ensure_tcg(self) -> int:
        stmt = (
            insert(Tcg)
            .values(code=TCG_CODE, name=TCG_NAME)
            .on_conflict_do_update(index_elements=["code"], set_={"name": TCG_NAME})
            .returning(Tcg.id)
        )
        self._tcg_id = (await self.session.execute(stmt)).scalar_one()
        return self._tcg_id

    # ------------------------------------------------------------------
    # Extensions
    # ------------------------------------------------------------------

    async def _upsert_expansion(
        self, payload: dict[str, Any], language: str, authoritative: bool
    ) -> int:
        code = payload["id"]
        compteurs = payload.get("cardCount") or {}
        serie = payload.get("serie") or {}

        valeurs: dict[str, Any] = {
            "tcg_id": self._tcg_id,
            "code": code,
            "series": serie.get("name"),
            "release_date": payload.get("releaseDate") or None,
            "card_count_official": compteurs.get("official"),
            "card_count_total": compteurs.get("total"),
            "symbol_url": payload.get("symbol"),
            "external_ids": {"tcgdex": code},
        }

        if authoritative:
            # La langue de référence écrase : c'est elle qui fait foi.
            maj = {k: v for k, v in valeurs.items() if k not in ("tcg_id", "code")}
        else:
            # Les autres langues ne touchent pas à l'invariant. On force une
            # écriture inerte pour récupérer l'id malgré le conflit.
            maj = {"tcg_id": self._tcg_id}

        stmt = (
            insert(Expansion)
            .values(**valeurs)
            .on_conflict_do_update(index_elements=["tcg_id", "code"], set_=maj)
            .returning(Expansion.id)
        )
        expansion_id = (await self.session.execute(stmt)).scalar_one()

        nom = payload.get("name")
        if nom:
            stmt_nom = insert(ExpansionName).values(
                expansion_id=expansion_id,
                language=language,
                name=nom,
                logo_url=payload.get("logo"),
            ).on_conflict_do_update(
                index_elements=["expansion_id", "language"],
                set_={"name": nom, "logo_url": payload.get("logo")},
            )
            await self.session.execute(stmt_nom)

        self.report.expansions[language] += 1
        return expansion_id

    # ------------------------------------------------------------------
    # Cartes
    # ------------------------------------------------------------------

    async def _upsert_card(
        self, payload: dict[str, Any], expansion_id: int, language: str, authoritative: bool
    ) -> int:
        numero = str(payload.get("localId") or payload.get("id"))
        invariants, localises = split_attributes(payload)

        valeurs: dict[str, Any] = {
            "expansion_id": expansion_id,
            "number": numero,
            "number_sort": number_sort(numero),
            "illustrator": payload.get("illustrator"),
            "attributes": invariants,
            "external_ids": {"tcgdex": payload["id"]},
        }

        if authoritative:
            valeurs["rarity_id"] = await self._rarity_id(payload.get("rarity"), language)
            valeurs["card_type_id"] = await self._card_type_id(
                payload.get("category"), language
            )
            maj = {k: v for k, v in valeurs.items() if k not in ("expansion_id", "number")}
        else:
            maj = {"expansion_id": expansion_id}

        stmt = (
            insert(Card)
            .values(**valeurs)
            .on_conflict_do_update(index_elements=["expansion_id", "number"], set_=maj)
            .returning(Card.id)
        )
        card_id = (await self.session.execute(stmt)).scalar_one()

        # Une langue non référente enrichit quand même les vocabulaires : c'est
        # ainsi que `rarity.labels` finit par contenir le français.
        if not authoritative:
            await self._attach_labels(card_id, payload, language)

        await self._upsert_localization(card_id, payload, language, localises)
        await self._upsert_printings(card_id, payload, language)

        self.report.cards[language] += 1
        return card_id

    async def _attach_labels(
        self, card_id: int, payload: dict[str, Any], language: str
    ) -> None:
        """Ajoute les libellés traduits aux vocabulaires déjà rattachés.

        On ne touche pas aux `*_id` de la carte (invariants) : on complète
        seulement les `labels` des lignes qu'elle référence.
        """
        ligne = (
            await self.session.execute(
                select(Card.rarity_id, Card.card_type_id).where(Card.id == card_id)
            )
        ).first()
        if not ligne:
            return
        rarity_id, card_type_id = ligne

        if rarity_id and payload.get("rarity"):
            await self.session.execute(
                insert(Rarity)
                .values(id=rarity_id, tcg_id=self._tcg_id, code="", labels={})
                .on_conflict_do_update(
                    index_elements=["id"],
                    set_={
                        "labels": Rarity.labels.op("||")(
                            {language: str(payload["rarity"])}
                        )
                    },
                )
            )
        if card_type_id and payload.get("category"):
            await self.session.execute(
                insert(CardType)
                .values(id=card_type_id, tcg_id=self._tcg_id, code="", labels={})
                .on_conflict_do_update(
                    index_elements=["id"],
                    set_={
                        "labels": CardType.labels.op("||")(
                            {language: str(payload["category"])}
                        )
                    },
                )
            )

    async def _upsert_localization(
        self, card_id: int, payload: dict[str, Any], language: str, attributs: dict[str, Any]
    ) -> None:
        nom = payload.get("name")
        if not nom:
            self.report.warn(payload.get("id", "?"), f"sans nom en '{language}', ignorée")
            return

        valeurs = {
            "card_id": card_id,
            "language": language,
            "name": nom,
            "image_url": payload.get("image"),
            "attributes": attributs,
        }
        stmt = insert(CardLocalization).values(**valeurs).on_conflict_do_update(
            index_elements=["card_id", "language"],
            set_={k: v for k, v in valeurs.items() if k not in ("card_id", "language")},
        )
        await self.session.execute(stmt)
        self.report.localizations[language] += 1

    async def _upsert_printings(
        self, card_id: int, payload: dict[str, Any], language: str
    ) -> None:
        specs, inconnus = extract_finishes(payload, language)
        for type_inconnu in inconnus:
            self.report.warn(
                payload.get("id", "?"), f"type de finition inconnu : {type_inconnu!r}"
            )
        if not specs:
            self.report.warn(payload.get("id", "?"), "aucune finition déclarée")
            return

        for spec in specs:
            finish_id = await self._finish_id(
                spec.finish_code, spec.labels.get(language), language
            )
            # Le format est une colonne, plus une clé d'`attributes` ni un
            # suffixe de finition : il fait partie de l'identité de
            # l'impression, donc de sa clé d'unicité (migration 0007).
            valeurs = {
                "card_id": card_id,
                "finish_id": finish_id,
                "language": language,
                "size": spec.size,
                "external_ids": spec.external_ids(),
            }
            stmt = insert(Printing).values(**valeurs).on_conflict_do_update(
                index_elements=["card_id", "finish_id", "language", "size"],
                set_={"external_ids": valeurs["external_ids"]},
            )
            await self.session.execute(stmt)
            self.report.printings[language] += 1

    # ------------------------------------------------------------------
    # Reprise
    # ------------------------------------------------------------------

    async def _done_expansions(self, language: str) -> set[str]:
        if self.force:
            return set()
        lignes = await self.session.execute(
            select(ImportCheckpoint.scope_code).where(
                ImportCheckpoint.source == SOURCE,
                ImportCheckpoint.language == language,
            )
        )
        return set(lignes.scalars())

    async def _mark_done(self, language: str, code: str, nb: int) -> None:
        stmt = insert(ImportCheckpoint).values(
            source=SOURCE, language=language, scope_code=code, cards_seen=nb
        ).on_conflict_do_update(
            index_elements=["source", "language", "scope_code"],
            set_={"cards_seen": nb},
        )
        await self.session.execute(stmt)

    # ------------------------------------------------------------------
    # Boucle principale
    # ------------------------------------------------------------------

    async def run(self) -> ImportReport:
        self.report.languages = list(self.languages)
        await self._ensure_tcg()
        await self.session.commit()

        for language in self.languages:
            authoritative = language == self.reference_language
            await self._import_language(language, authoritative)

        await self._collect_totals()
        return self.report

    async def _collect_totals(self) -> None:
        """Compte les lignes réellement présentes, pour le rapport.

        Les compteurs d'opérations ne disent pas combien de cartes existent :
        une carte vue en `en` puis en `fr` y compte deux fois. Sans ces
        totaux-ci, le rapport annonçait 45 528 cartes pour 23 649 lignes, et
        toute vérification manuelle partait sur un faux écart.
        """
        for nom, modele in (
            ("expansion", Expansion),
            ("card", Card),
            ("card_localization", CardLocalization),
            ("printing", Printing),
        ):
            total = await self.session.scalar(
                select(func.count()).select_from(modele)
            )
            self.report.totals[nom] = int(total or 0)

    async def _import_language(self, language: str, authoritative: bool) -> None:
        try:
            sets = await self.client.list_sets(language)
        except Exception as exc:
            self.report.error(f"sets/{language}", str(exc))
            return

        deja_faites = await self._done_expansions(language)
        logger.info(
            "Langue '%s' : %s extensions (%s déjà importées)",
            language, len(sets), len(deja_faites),
        )

        for entree in sets:
            code = entree.get("id")
            if not code:
                continue
            if self.expansion_filter and code not in self.expansion_filter:
                continue
            if code in deja_faites:
                self.report.skipped_expansions += 1
                continue
            try:
                await self._import_expansion(language, code, authoritative)
            except Exception as exc:  # noqa: BLE001 — une extension en échec
                # ne doit pas emporter tout l'import ; elle remonte au rapport.
                await self.session.rollback()
                self.report.error(f"{language}/{code}", str(exc))
                logger.warning("Extension %s/%s en échec : %s", language, code, exc)

    async def _import_expansion(self, language: str, code: str, authoritative: bool) -> None:
        detail = await self.client.get_set(language, code)
        expansion_id = await self._upsert_expansion(detail, language, authoritative)

        brefs = detail.get("cards") or []
        # Le détail de chaque carte est récupéré en parallèle (le client borne
        # la concurrence), puis écrit séquentiellement : une seule session
        # SQLAlchemy, donc une seule transaction cohérente par extension.
        charges = await asyncio.gather(
            *(self._fetch_card(language, c.get("id")) for c in brefs if c.get("id")),
            return_exceptions=False,
        )

        nb = 0
        for payload in charges:
            if payload is None:
                continue
            await self._upsert_card(payload, expansion_id, language, authoritative)
            nb += 1

        await self._mark_done(language, code, nb)
        await self.session.commit()
        logger.info("  %s/%s : %s cartes", language, code, nb)

    async def _fetch_card(self, language: str, card_id: str) -> dict[str, Any] | None:
        try:
            return await self.client.get_card(language, card_id)
        except NotFoundError:
            # La carte n'existe pas dans cette langue : c'est un fait, pas une
            # panne. On le note et on continue.
            self.report.warn(card_id, f"absente en '{language}'")
            return None
        except Exception as exc:  # noqa: BLE001
            self.report.error(card_id, f"{language} : {exc}")
            return None

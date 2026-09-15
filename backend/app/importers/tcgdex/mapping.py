"""Traduction d'une charge utile TCGdex vers nos entités.

Module **pur** : aucune base, aucun réseau. C'est le point délicat du lot 2,
donc il est isolé pour être lisible et testable seul.

Ce que l'exploration du 15/09/2026 a établi sur les 23 548 cartes du corpus,
et qui justifie tout ce qui suit :

1. TCGdex décrit les finitions **deux fois, sans que l'une recouvre l'autre** :

   - `variants` — 5 booléens aux clés stables (`normal`, `reverse`, `holo`,
     `firstEdition`, `wPromo`). Seule source pour `firstEdition` (938 cartes).
     `wPromo` n'est jamais vrai dans le corpus actuel.
   - `variants_detailed` — la liste des variantes commerciales, avec les
     identifiants Cardmarket / TCGplayer. Seule source pour `lenticular`,
     `metal` et la taille `jumbo` (219 cartes). N'expose **jamais**
     `firstEdition`.

   Les deux divergent sur 13 % des cartes. On prend donc **l'union**.

2. `variants_detailed[].type` et `.size` sont **localisés** (`Normal`/`normal`,
   `Métal`/`metal`). Le code d'une finition ne doit jamais dépendre de la langue
   importée : on normalise (minuscules, sans accents) et on résout contre une
   liste fermée. Un type inconnu devient un avertissement, **jamais** un code
   inventé.

3. `variantId` n'est **pas** un identifiant d'impression, contrairement à ce que
   son nom suggère : `endfynwn4n10gzq` est porté par 8 914 cartes, et la valeur
   littérale `generated` par 8 861. C'est un identifiant de *type* de variante.
   On ne s'en sert donc pas comme clé — seuls les identifiants Cardmarket et
   TCGplayer sont discriminants.

4. 1 762 cartes portent deux entrées `normal/standard` strictement identiques
   avec des identifiants Cardmarket différents. Le schéma impose
   `UNIQUE (carte, finition, langue)` : on crée **une** impression et on
   conserve **tous** les identifiants en liste. Rien n'est perdu, l'ambiguïté
   reste visible, et le lot 5 tranchera.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Any

# --- Vocabulaire fermé des finitions -------------------------------------

# Clés de `variants` → notre code. Invariantes par langue : c'est le socle.
BOOLEAN_FINISHES: dict[str, str] = {
    "normal": "normal",
    "reverse": "reverse",
    "holo": "holo",
    "firstEdition": "first_edition",
    "wPromo": "w_promo",
}

# Valeurs normalisées de `variants_detailed[].type` → notre code.
DETAILED_FINISHES: dict[str, str] = {
    "normal": "normal",
    "reverse": "reverse",
    "holo": "holo",
    "lenticular": "lenticular",
    "metal": "metal",
}

# Libellé anglais de repli pour les finitions qui ne viennent QUE des booléens.
# `variants` n'expose pas de libellé, seulement des clés : sans ce repli,
# `first_edition` et `w_promo` arrivaient en base avec `labels = {}`, seules de
# tout le vocabulaire à n'avoir aucun nom affichable.
# Ce sont des libellés de repli, pas des traductions : l'anglais sert de socle,
# et un libellé venu de `variants_detailed` le remplace quand il existe.
BOOLEAN_FINISH_LABELS: dict[str, str] = {
    "normal": "Normal",
    "reverse": "Reverse",
    "holo": "Holo",
    "first_edition": "1st Edition",
    "w_promo": "W Promo",
}

# Format considéré comme la norme. Il ne suffixe rien : la taille est une
# colonne de `printing`, membre de sa clé d'unicité (migration 0006).
DEFAULT_SIZE = "standard"

# Champs de la charge utile **invariants par langue** (vérifié en comparant la
# même carte en FR et en EN). Tout le reste descend dans la localisation.
INVARIANT_ATTRIBUTES = ("hp", "retreat", "dexId", "regulationMark", "level")
LOCALIZED_ATTRIBUTES = (
    "stage",
    "types",
    "evolveFrom",
    "attacks",
    "weaknesses",
    "resistances",
    "abilities",
    "description",
    "effect",
    "trainerType",
    "energyType",
    "suffix",
)


def normalize_code(value: str | None) -> str | None:
    """Minuscules, sans accents, espaces en tirets bas.

    Sert à fabriquer un code stable à partir d'un libellé qui, lui, peut être
    traduit : « Métal » et « metal » doivent donner le même code.
    """
    if value is None:
        return None
    texte = unicodedata.normalize("NFKD", str(value))
    texte = "".join(ch for ch in texte if not unicodedata.combining(ch))
    texte = texte.strip().lower()
    texte = re.sub(r"[^a-z0-9]+", "_", texte).strip("_")
    return texte or None


def number_sort(local_id: str | None) -> int | None:
    """Part numérique d'un numéro de carte, pour le tri.

    Les numéros réels ne sont pas des nombres : « 025 », « SV49 », « TG12 »,
    « H1 ». On extrait la première suite de chiffres ; sans chiffre, on renvoie
    None plutôt qu'un zéro qui fausserait le tri.
    """
    if not local_id:
        return None
    m = re.search(r"\d+", str(local_id))
    if not m:
        return None
    try:
        valeur = int(m.group())
    except ValueError:
        return None
    # Garde-fou : la colonne est un INTEGER Postgres.
    return valeur if -(2**31) <= valeur < 2**31 else None


class FinishSpec:
    """Une finition retenue pour une carte, et ses identifiants externes."""

    __slots__ = ("code", "size", "labels", "cardmarket_ids", "tcgplayer_ids", "variant_ids")

    def __init__(self, code: str, size: str) -> None:
        self.code = code
        self.size = size
        self.labels: dict[str, str] = {}
        self.cardmarket_ids: list[int] = []
        self.tcgplayer_ids: list[int] = []
        self.variant_ids: list[str] = []

    @property
    def finish_code(self) -> str:
        """Le code stocké en base, sans le format.

        Une jumbo n'a ni la même cote ni la même place, mais ça n'en fait pas
        une autre *finition* : depuis la migration 0006, le format est une
        colonne de `printing` et un membre de sa clé d'unicité. Le suffixer ici
        dupliquerait l'information et gonflerait le vocabulaire de `finish` à
        chaque format rencontré.
        """
        return self.code

    def external_ids(self) -> dict[str, Any]:
        """Identifiants externes de l'impression.

        Les listes sont conservées **entières** : plusieurs identifiants
        Cardmarket pour une même finition sont le reflet fidèle de la source
        (cf. point 4 de l'en-tête), pas un doublon à écraser.
        """
        ids: dict[str, Any] = {}
        if self.cardmarket_ids:
            ids["cardmarket"] = sorted(set(self.cardmarket_ids))
        if self.tcgplayer_ids:
            ids["tcgplayer"] = sorted(set(self.tcgplayer_ids))
        if self.variant_ids:
            # Conservés pour traçabilité, jamais comme clé (point 3).
            ids["tcgdexVariantIds"] = sorted(set(self.variant_ids))
        return ids

    def __repr__(self) -> str:
        return f"<FinishSpec {self.finish_code}>"


def extract_finishes(
    payload: dict[str, Any], language: str
) -> tuple[list[FinishSpec], list[str]]:
    """Union de `variants` et `variants_detailed`.

    Renvoie les finitions retenues et la liste des types inconnus rencontrés —
    ces derniers remontent au rapport plutôt que d'être devinés.
    """
    specs: dict[tuple[str, str], FinishSpec] = {}
    inconnus: list[str] = []

    def obtenir(code: str, size: str) -> FinishSpec:
        cle = (code, size)
        if cle not in specs:
            spec = FinishSpec(code, size)
            # Libellé de repli posé dès la création : les finitions qui ne
            # viennent que des booléens n'en recevraient jamais autrement, la
            # boucle `variants_detailed` étant seule à en fournir.
            repli = BOOLEAN_FINISH_LABELS.get(code)
            if repli:
                spec.labels["en"] = repli
            specs[cle] = spec
        return specs[cle]

    # 1. Les booléens : socle invariant, seule source de `firstEdition`.
    for cle, actif in (payload.get("variants") or {}).items():
        if actif and cle in BOOLEAN_FINISHES:
            obtenir(BOOLEAN_FINISHES[cle], DEFAULT_SIZE)

    # 2. Les variantes détaillées : tailles, types rares, identifiants de prix.
    for entree in payload.get("variants_detailed") or []:
        type_brut = entree.get("type")
        code_norm = normalize_code(type_brut)
        code = DETAILED_FINISHES.get(code_norm or "")
        if code is None:
            if type_brut:
                inconnus.append(str(type_brut))
            continue

        size = normalize_code(entree.get("size")) or DEFAULT_SIZE
        spec = obtenir(code, size)

        # Le libellé, lui, est traduit : on le range par langue.
        if type_brut:
            spec.labels[language] = str(type_brut)

        tiers = entree.get("thirdParty") or {}
        if isinstance(tiers.get("cardmarket"), int):
            spec.cardmarket_ids.append(tiers["cardmarket"])
        if isinstance(tiers.get("tcgplayer"), int):
            spec.tcgplayer_ids.append(tiers["tcgplayer"])
        vid = entree.get("variantId")
        if vid and vid != "generated":
            spec.variant_ids.append(str(vid))

    return list(specs.values()), inconnus


def split_attributes(payload: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    """Sépare les caractéristiques invariantes de celles qui changent de langue.

    La distinction vient de la comparaison FR/EN d'une même carte : `hp`,
    `retreat`, `dexId` et `regulationMark` sont identiques, alors que le stade,
    les types et les attaques sont traduits.
    """
    invariants = {
        k: payload[k] for k in INVARIANT_ATTRIBUTES if payload.get(k) is not None
    }
    localises = {
        k: payload[k] for k in LOCALIZED_ATTRIBUTES if payload.get(k) is not None
    }
    return invariants, localises

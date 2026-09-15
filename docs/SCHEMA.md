# Schéma de données — lot 1

Modèle de référence multi-TCG. Il tient en **7 tables de domaine** et
**3 tables de référence**.

La décision structurante tient en une phrase : **une CARTE n'a pas de prix,
une IMPRESSION en a un.** Tout le reste en découle.

---

## Vue d'ensemble

```mermaid
erDiagram
    TCG              ||--o{ EXPANSION        : "publie"
    TCG              ||--o{ RARITY           : "définit"
    TCG              ||--o{ FINISH           : "définit"
    EXPANSION        ||--o{ CARD             : "contient"
    RARITY           |o--o{ CARD             : "qualifie"
    CARD             ||--o{ CARD_NAME        : "se nomme (1 par langue)"
    CARD             ||--o{ PRINTING         : "se décline en"
    FINISH           ||--o{ PRINTING         : "qualifie"
    PRINTING         ||--o{ PRICE_SNAPSHOT   : "est coté"
    PRICE_SOURCE     ||--o{ PRICE_SNAPSHOT   : "cote"
    PRINTING         ||--o{ COLLECTION_ITEM  : "est possédé"
    PRINTING         ||--o| WATCHED_PRINTING : "est surveillé"

    TCG { int id PK; string code UK; string name }
    EXPANSION { int id PK; int tcg_id FK; string code; string name; string series; date release_date; int card_count_official; int card_count_total; jsonb external_ids }
    CARD { int id PK; int expansion_id FK; string number; int number_sort; int rarity_id FK; string illustrator; text image_url; string image_phash; jsonb external_ids }
    CARD_NAME { int id PK; int card_id FK; string language; string name; text name_normalized }
    PRINTING { int id PK; int card_id FK; int finish_id FK; string language; jsonb external_ids }
    COLLECTION_ITEM { int id PK; int printing_id FK; int quantity; enum condition; numeric purchase_price; string purchase_currency; date purchase_date; text notes }
    PRICE_SNAPSHOT { int id PK; int printing_id FK; int source_id FK; date observed_on; timestamptz fetched_at; numeric amount; string currency; string price_type; jsonb raw }
    WATCHED_PRINTING { int id PK; int printing_id FK UK; text notes }
    RARITY { int id PK; int tcg_id FK; string code; string label; int sort_order }
    FINISH { int id PK; int tcg_id FK; string code; string label; int sort_order }
    PRICE_SOURCE { int id PK; string code UK; string label; string default_currency; text homepage_url }
```

---

## Le cœur : CARD vs PRINTING

Prenons Dracaufeu 4/102 du Set de Base.

| Niveau | Ce que c'est | Combien d'objets |
|---|---|---|
| `CARD` | Dracaufeu, n° 4 du Set de Base, illustré par Mitsuhiro Arita | **1** |
| `CARD_NAME` | « Dracaufeu » (fr), « Charizard » (en), « リザードン » (ja) | **1 par langue** |
| `PRINTING` | la holo anglaise, la holo française, la 1st edition anglaise… | **1 par (finition, langue)** |
| `COLLECTION_ITEM` | mes 2 exemplaires en NM achetés 180 € | **1 par lot acheté** |

Un prix ne s'attache **jamais** à `CARD`. Une reverse holo et une normale sont
la même carte et n'ont pas la même valeur — parfois d'un facteur dix. Rattacher
la cote à `CARD` rendrait toute valorisation fausse, sans rattrapage possible.

Symétriquement, le nom ne s'attache pas non plus à `CARD` : il vit dans
`CARD_NAME`, une ligne par langue. C'est ce qui permet à « dracaufeu » et à
« charizard » de retomber sur la même carte au lot 3.

---

## Recherche floue : comment `NOM_LOCALISE` est indexé

La recherche doit être insensible à la casse, aux accents, et tolérer les
fautes de frappe. Trois pièces s'emboîtent :

**1. Une fonction `immutable_unaccent`** (migration `0002`).
`unaccent()` est déclarée `STABLE` par PostgreSQL, parce que son résultat
dépend du dictionnaire installé. Or une colonne générée exige `IMMUTABLE`.
On l'enveloppe donc en fixant le dictionnaire explicitement :

```sql
CREATE FUNCTION immutable_unaccent(text) RETURNS text
LANGUAGE sql IMMUTABLE STRICT PARALLEL SAFE
AS $$ SELECT unaccent('unaccent'::regdictionary, $1) $$;
```

> **Contrepartie assumée.** Le wrapper affirme une immuabilité que PostgreSQL
> ne garantit pas. Si le dictionnaire `unaccent` était un jour modifié, les
> valeurs déjà calculées ne seraient pas recalculées et l'index deviendrait
> faux. En pratique ce dictionnaire ne bouge pas ; le jour où on y toucherait,
> il faudrait un `REINDEX` et un `UPDATE` des colonnes générées. C'est le
> compromis standard pour indexer une recherche insensible aux accents.

**2. Une colonne générée** `card_name.name_normalized` :

```sql
name_normalized text GENERATED ALWAYS AS (lower(immutable_unaccent(name))) STORED
```

Calculée par la base, pas par Python. La normalisation ne peut donc jamais
diverger du nom, quel que soit le chemin d'écriture — import, API, ou `psql`
à la main.

**3. Un index GIN trigram** sur cette colonne, et non sur `name` :

```sql
CREATE INDEX ix_card_name_normalized_trgm
    ON card_name USING gin (name_normalized gin_trgm_ops);
```

Indexer `name` aurait été inutile : une recherche sur la forme sans accent
n'aurait pas pu s'en servir.

Résultat mesuré sur le jeu de test :

```
similarity('dracaufeu',  'dracofeu')  = 0.462   → trouvé
similarity('charizard', 'sharizard')  = 0.538   → trouvé
```

> L'**usage effectif** de l'index (et non un parcours séquentiel) ne pourra se
> vérifier qu'au lot 2, une fois le référentiel chargé : sur quelques lignes,
> PostgreSQL ignore l'index à raison.

---

## Les trois tables de référence, et pourquoi ce ne sont pas des ENUM

`RARITY`, `FINISH` et `PRICE_SOURCE` sont des tables, pas des types `ENUM`.

Pour `FINISH`, c'est le point le plus sensible du lot : **on ne connaîtra le
vocabulaire réel des finitions qu'au lot 2**, après exploration de TCGdex.
Riftbound en apportera d'autres encore. Un `ENUM` imposerait une migration à
chaque découverte ; une table absorbe une nouvelle valeur par un `INSERT`.

`RARITY` suit la même logique : Pokémon en compte des dizaines, et la liste
s'allonge à chaque extension.

Les deux sont **rattachées à un TCG** (`UNIQUE (tcg_id, code)`) : chaque jeu
déclare son propre vocabulaire, y compris son propre « normal ».

À l'inverse, `condition` **est** un `ENUM` PostgreSQL (`MT`, `NM`, `EX`, `GD`,
`LP`, `PL`, `PO`). L'échelle Cardmarket est fermée et stable depuis des
années : ici le typage strict apporte plus qu'il ne coûte, et la base refuse
un état invalide.

---

## Les prix

### Une ligne = un point de valorisation

Les sources renvoient plusieurs métriques pour une même carte (tendance,
moyenne 7 jours, plus bas…). Deux modélisations étaient possible :

| | Une ligne par métrique | Une ligne canonique + `raw` |
|---|---|---|
| Valorisation | jointure + choix de métrique à chaque requête | somme directe |
| Volume | × 5 à 8 | × 1 |
| Perte d'information | aucune | aucune (`raw` garde tout) |

On a retenu la seconde : `amount` + `currency` portent le montant canonique,
`price_type` dit quelle métrique a été retenue (`cardmarket.trendPrice`…),
et `raw` (JSONB) conserve la réponse complète de la source. Si on change
d'avis sur la métrique canonique, l'historique reste rejouable depuis `raw`.

### Granularité au jour, et job rejouable

`UNIQUE (printing_id, source_id, observed_on)` : un relevé par impression, par
source et par jour. Le job quotidien du lot 5 peut donc être relancé sans rien
dupliquer :

```sql
INSERT INTO price_snapshot (...) VALUES (...)
ON CONFLICT (printing_id, source_id, observed_on)
DO UPDATE SET amount = EXCLUDED.amount, fetched_at = now();
```

`observed_on` (date de cotation de la source) et `fetched_at` (instant de notre
appel) sont deux choses différentes : le second permet de distinguer « la
source n'a pas bougé » de « on n'a pas relevé ».

### Ce que le job doit relever

Le lot 5 impose de ne relever **que les impressions possédées ou surveillées**,
jamais l'intégralité du référentiel. D'où `WATCHED_PRINTING`, sans laquelle
« surveiller une carte avant de l'acheter » serait impossible. La requête du
job tient en deux lignes :

```sql
SELECT printing_id FROM collection_item
UNION
SELECT printing_id FROM watched_printing;
```

---

## Intégrité : ce que la base refuse

Toutes ces règles ont été vérifiées en SQL (voir « Vérification » plus bas).

| Règle | Mécanisme |
|---|---|
| Quantité nulle ou négative | `CHECK (quantity > 0)` |
| Montant négatif | `CHECK (amount >= 0)`, `CHECK (purchase_price >= 0)` |
| Prix sans devise, ou devise sans prix | `CHECK ((purchase_price IS NULL) = (purchase_currency IS NULL))` |
| État hors échelle Cardmarket | type `ENUM card_condition` |
| Deux impressions identiques | `UNIQUE (card_id, finish_id, language)` |
| Deux noms dans la même langue | `UNIQUE (card_id, language)` |
| Deux relevés le même jour | `UNIQUE (printing_id, source_id, observed_on)` |
| Deux cartes au même numéro dans un set | `UNIQUE (expansion_id, number)` |

### Cascades : ce qui s'efface, ce qui résiste

Le choix n'est pas uniforme, et c'est volontaire :

- `CARD_NAME`, `PRINTING`, `PRICE_SNAPSHOT` → **CASCADE**. Ce sont des données
  dérivées du référentiel ; si la carte disparaît, elles n'ont plus de sens.
- `COLLECTION_ITEM` → **RESTRICT**. Ma collection n'est pas dérivée : c'est la
  seule donnée que je saisis moi-même et que personne ne peut reconstruire. Un
  réimport qui ferait disparaître une impression doit **échouer bruyamment**,
  pas effacer en silence ce que je possède.
- `CARD.rarity_id` → **SET NULL**. Une rareté renommée ne doit pas emporter
  les cartes avec elle.

---

## Identifiants externes

`EXPANSION`, `CARD` et `PRINTING` portent une colonne `external_ids` en JSONB,
indexée en GIN :

```json
{ "tcgdex": "swsh3-25", "pokemontcgio": "swsh3-25" }
```

Elle sert à deux choses : rendre l'import du lot 2 **idempotent** (retrouver
une carte déjà importée par son id source, sans scan complet), et **croiser
les sources** au lot 5 — TCGdex fournit le référentiel, pokemontcg.io les
prix, et il faut pouvoir passer de l'un à l'autre.

`PRINTING.external_ids` n'était pas demandé par la spec ; il est là parce que
le lot 5 devra noter, pour chaque impression, sous quelle clé de variante la
source range sa cote.

---

## Détails qui méritent un mot

**`CARD.number` est du texte, doublé d'un `number_sort` entier.** Les numéros
réels ne sont pas des nombres : `025`, `SV49`, `TG12`, `H1`. Les stocker en
entier perdrait de l'information ; les trier en texte classerait « 10 » avant
« 2 ». D'où les deux colonnes — le numéro imprimé, et sa part numérique pour
le tri.

**`EXPANSION` porte deux compteurs.** `card_count_official` est le total
imprimé sur la carte (le 102 de « 4/102 ») ; `card_count_total` est le nombre
réel de cartes du set, cartes secrètes comprises. La complétion du lot 4 a
besoin des deux, et ils diffèrent presque toujours.

**`CARD.image_phash` est déjà là, vide.** Le lot 7 précalculera un hash
perceptuel sur toutes les illustrations. La colonne est posée maintenant parce
que l'ajouter plus tard sur une table à 20 000 lignes coûte une migration
lourde ; vide, elle ne coûte rien.

**`language` est une colonne texte, pas un `ENUM`.** Codes façon BCP 47
(`fr`, `en`, `ja`, `zh-tw`). Ajouter une langue ne doit pas demander de
migration.

**Tous les montants sont en `NUMERIC(12,2)`.** Jamais de flottant : en binaire,
`0.1 + 0.2 ≠ 0.3`, et un total de collection finit par être faux de quelques
centimes sans qu'on sache pourquoi.

---

## Ce que le schéma n'a pas (et comment l'ajouter)

**Pas d'utilisateurs.** L'authentification multi-utilisateurs est hors
périmètre. Le jour venu, la migration est simple : une table `user`, puis une
colonne `user_id` sur `COLLECTION_ITEM` et `WATCHED_PRINTING` — les deux seules
tables qui portent des données personnelles. Le reste du schéma, référentiel et
prix, est partagé par nature et ne bouge pas.

**Pas de cartes gradées** (PSA, BGS). C'est un axe distinct de `condition` :
il faudrait un `grading_company` et un `grade` sur `COLLECTION_ITEM`. Rien dans
le modèle actuel ne s'y oppose.

**Riftbound n'est pas modélisé, et n'a pas à l'être.** Il lui faut une ligne
dans `TCG`, ses propres `FINISH` et `RARITY`, et un import qui remplisse
`EXPANSION` / `CARD` / `CARD_NAME`. Aucune migration de schéma.

---

## Vérification

Le schéma a été validé sur la base réelle : cycle `upgrade` → `downgrade` →
`upgrade` complet, puis douze tests SQL dans une transaction annulée (colonne
générée, recherche trigram avec fautes, deux prix pour deux finitions d'une
même carte, et les huit contraintes du tableau ci-dessus, chacune vérifiée en
constatant que la base **refuse** bien l'écriture).

```bash
docker compose exec api alembic upgrade head
docker compose exec db psql -U tcg -d tcg -c "\dt"
```

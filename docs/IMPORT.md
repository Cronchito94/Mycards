# Import du référentiel — lot 2

Import idempotent du référentiel Pokémon depuis **TCGdex**.

> Ce document consigne aussi **ce que renvoie réellement l'API**, relevé sur
> l'intégralité du corpus le 15/09/2026. Plusieurs constats contredisent ce
> qu'on pouvait supposer de la documentation : ils sont signalés comme tels.

---

## Conditions d'utilisation — vérifié avant tout appel

| Point | Constat |
|---|---|
| Licence | **MIT**, pour le code *et* la base de données (`tcgdex/cards-database`) |
| Redistribution | Autorisée par la MIT, avec conservation de la notice |
| Affiliation | Le dépôt précise : *« not produced, endorsed, supported or affiliated with Nintendo or The Pokémon Company »* |
| Débit imposé | Aucune limite publiée dans le dépôt |
| Images | Servies par `assets.tcgdex.net`. **On ne stocke que les URLs** — jamais les fichiers (droits des éditeurs) |

Rien n'interdit notre usage. La prudence reste de mise sur les **images**, qui
relèvent du droit des éditeurs et non de la licence de la base.

---

## Auto-hébergé plutôt que l'API publique

Le `docker-compose.yml` embarque un service `tcgdex` (image `tcgdex/server:edge`,
MIT, ~81 Mo, données incluses). C'est le choix par défaut :

- **Pas de limite de débit** : l'import complet passe de plusieurs heures à
  8 minutes (mesuré : 513 s pour 45 528 cartes).
- **Pas de dépendance réseau** pendant l'import.
- **Insensible à l'inspection TLS** de certains postes, qui casse les appels
  sortants.

Pour viser l'API publique à la place :

```bash
docker compose exec api python -m app.importers.cli \
    --base-url https://api.tcgdex.net --rate-limit 10
```

> ⚠️ **`CI=true` est obligatoire** sur le conteneur `tcgdex`. Sans cette
> variable, le serveur télécharge les tarifs Cardmarket et TCGplayer *avant*
> d'ouvrir son port HTTP. Derrière une inspection TLS ces appels échouent en
> boucle : le conteneur paraît sain, les logs affichent même « Server ready »,
> mais rien n'écoute sur le 3000. Les prix relèvent du lot 5, pas d'ici.

> ⚠️ L'amont ne publie **aucun tag versionné** (`edge` et `branch-master`
> seulement) : revérifier que l'API répond après chaque `docker compose pull`.

---

## Utilisation

```bash
# Import complet FR + EN (idempotent : relançable sans risque)
docker compose exec api python -m app.importers.cli --languages en,fr

# Un essai rapide sur une seule extension
docker compose exec api python -m app.importers.cli --expansions swsh3

# Tout réimporter, y compris ce qui est marqué terminé
docker compose exec api python -m app.importers.cli --force
```

| Option | Rôle |
|---|---|
| `--languages` | Langues, séparées par des virgules. **La première fait foi** (voir plus bas) |
| `--force` | Ignore les points de reprise |
| `--expansions` | Restreint à des codes d'extension |
| `--base-url` | Vise une autre instance TCGdex |
| `--rate-limit` | Requêtes/seconde (`0` = illimité, local uniquement) |

Le code de sortie vaut `1` si le rapport contient la moindre erreur : un import
partiel n'est pas un succès.

---

## La langue de référence

**La première langue de `--languages` fait foi pour tout ce qui est invariant** :
numéro, illustrateur, code de rareté, liste des finitions. Les suivantes
n'apportent que des localisations et des libellés traduits.

La raison est concrète : TCGdex renvoie la rareté **traduite**. Un premier
import en français donnerait un code de rareté `peu_commune`, et l'interface
serait figée en français pour toujours. D'où `--languages en,fr` par défaut.

Les libellés, eux, **fusionnent** au fil des passes :

```sql
SELECT code, labels FROM rarity WHERE code = 'uncommon';
--  uncommon | {"en": "Uncommon", "fr": "Peu Commune"}
```

**102 cartes n'existent qu'en français** (trois collections McDonald's). Elles
sont importées avec le français pour référence, faute de mieux. C'est visible
en base, et c'est assumé.

---

## Le mapping des finitions — le point délicat

### Ce que l'exploration a révélé

TCGdex décrit les finitions **deux fois, et aucune des deux descriptions ne
recouvre l'autre** :

| | `variants` (5 booléens) | `variants_detailed` (liste) |
|---|---|---|
| Clés stables par langue | ✅ | ❌ (`Normal` en FR, `normal` en EN) |
| `firstEdition` | ✅ **seule source** (938 cartes) | ❌ jamais présent |
| `lenticular`, `metal` | ❌ | ✅ **seule source** |
| Format `jumbo` (→ `printing.size`) | ❌ | ✅ **seule source** (210 entrées) |
| Identifiants Cardmarket / TCGplayer | ❌ | ✅ |

**Les deux divergent sur 13 % des cartes.** On prend donc l'union — décision
validée le 15/09/2026.

### Trois pièges, mesurés et non supposés

**1. `variantId` n'est pas un identifiant d'impression.** Son nom le suggère,
le relevé le contredit : `endfynwn4n10gzq` est porté par **8 914 cartes**, et
la valeur littérale `generated` par **8 861**. C'est un identifiant de *type*
de variante. Il est conservé pour traçabilité, jamais utilisé comme clé.

**2. Les libellés sont traduits, les codes ne doivent pas l'être.** `Métal` et
`metal` désignent la même finition. Le code est normalisé (minuscules, accents
supprimés) et résolu contre une liste **fermée** ; un type inconnu devient un
avertissement dans le rapport, **jamais** un code inventé.

**3. 1 762 cartes portent deux entrées de même finition** (`normal/standard`)
avec des identifiants Cardmarket différents — de vraies impressions distinctes
que TCGdex ne sait pas nommer. Le schéma impose `UNIQUE (carte, finition,
langue)` : on crée **une** impression et on conserve **tous** les identifiants
en liste.

```sql
SELECT external_ids FROM printing WHERE id = …;
-- {"cardmarket": [720365, 733596], "tcgplayer": [502552], …}
```

Rien n'est perdu, l'ambiguïté reste visible, et le lot 5 tranchera laquelle
coter. Aucune migration nécessaire.

### Le résultat

```
     code      |   size   | impressions
---------------+----------+-------------
 normal        | standard |       34554
 reverse       | standard |       15924
 holo          | standard |       11819
 first_edition | standard |        1614   ← issu des seuls booléens
 holo          | jumbo    |         298
 normal        | jumbo    |          46
 reverse       | jumbo    |          24
 lenticular    | jumbo    |          18   ← issu des seules variantes détaillées
 metal         | standard |           4   ← idem
```

**Six finitions, deux formats.** Le format est une colonne de `printing`
(`size`) et un membre de sa clé d'unicité, pas un suffixe de finition : une
jumbo est bien une impression distincte — deux cotes sans rapport — mais ce
n'est pas une autre *finition*. Voir la migration `0006`, qui a ramené neuf
codes à six.

`first_edition` ne vient que des booléens, et `variants` ne fournit aucun
libellé. Plutôt que de le laisser avec des `labels` vides — seule entrée du
vocabulaire à n'avoir aucun nom affichable — l'import pose un libellé anglais
de repli (`BOOLEAN_FINISH_LABELS`). Ce n'est pas une traduction inventée : un
libellé venu de `variants_detailed` le remplace dès qu'il existe.

`w_promo` existe dans le vocabulaire de TCGdex mais **n'est jamais vrai** dans
le corpus actuel : aucune finition n'a donc été créée pour lui.

---

## Idempotence et reprise

Deux mécanismes distincts, souvent confondus :

**L'idempotence** vient des upserts. Chaque écriture est un
`INSERT … ON CONFLICT DO UPDATE` sur une clé naturelle — `(tcg, code)` pour une
extension, `(extension, numéro)` pour une carte, `(carte, finition, langue)`
pour une impression. Relancer l'import ne duplique rien et corrige ce qui a
changé en amont. Vérifié : un `--force` sur une extension retraite 402 cartes
et laisse exactement 201 lignes.

**La reprise** vient de la table `import_checkpoint`, qui note l'extension
terminée par langue. Sans elle, une reprise referait les 45 000 requêtes HTTP.
Avec, elle ne reprend que ce qui manque. `--force` l'ignore.

---

## Résultat de l'import complet

```
  extensions           418   (en=218  fr=200)
  cartes             45528   (en=23547  fr=21981)
  localisations      45528
  impressions        64301
  vocabulaires          52   (card_type=3  finish=9  rarity=40)
  durée              513.0 s
  Aucune erreur.
```

En base : **23 649 cartes**, dont 21 879 bilingues, 1 668 en anglais seulement
et 102 en français seulement.

### Une anomalie de la source, signalée et non masquée

Une carte de l'extension `exu` a pour identifiant littéral `?`
(`exu-%3F` une fois encodé) et renvoie 404 dans les deux langues. Elle apparaît
en avertissement dans le rapport. C'est une donnée cassée en amont : on la
signale, on ne la fabrique pas.

---

## Vérifier en SQL

```sql
-- Volumétrie
SELECT count(*) FROM card;               -- 23649
SELECT count(*) FROM printing;           -- 64301

-- Couverture par langue
SELECT language, count(*) FROM card_localization GROUP BY language;

-- Le critère de fin : « dracaufeu » et « charizard », mêmes cartes
SELECT c.id, e.code, c.number,
       max(cl.name) FILTER (WHERE cl.language='fr') AS fr,
       max(cl.name) FILTER (WHERE cl.language='en') AS en
  FROM card c
  JOIN expansion e ON e.id = c.expansion_id
  JOIN card_localization cl ON cl.card_id = c.id
 WHERE c.id IN (SELECT card_id FROM card_localization
                 WHERE name_normalized = 'dracaufeu')
 GROUP BY c.id, e.code, c.number;

-- Recherche floue tolérante aux fautes (index trigram)
SELECT DISTINCT name, round(similarity(name_normalized,'dracofeu')::numeric,3) AS score
  FROM card_localization WHERE name_normalized % 'dracofeu'
 ORDER BY score DESC LIMIT 5;
```

L'index trigram est bien utilisé une fois le référentiel chargé — point ouvert
du lot 3 désormais fermé :

```
Bitmap Index Scan on ix_card_localization_normalized_trgm
Execution Time: 1.317 ms      (45 528 lignes)
```

---

## Tests

Le mapping est un module **pur** (`app/importers/tcgdex/mapping.py`) : ni base,
ni réseau. Ses cas de test reprennent les configurations réellement observées
dans le corpus, pas des exemples inventés.

```bash
cd backend && PYTHONPATH=. python -m pytest tests/ -q
```

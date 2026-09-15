# API de recherche — lot 3

Endpoints REST sur le référentiel. Préfixe `/api/v1`.
Documentation interactive : <http://localhost:8000/docs>.

---

## Le critère de fin, vérifié

> « Je cherche *dracaufeu* ou *charizard* et j'obtiens les mêmes cartes. »

```bash
curl 'localhost:8000/api/v1/cards?q=dracaufeu&page_size=2'
curl 'localhost:8000/api/v1/cards?q=charizard&page_size=2'
```

Les deux renvoient `id=809`, `base1`, n°4, avec `score=1.0`.

Mesuré sur l'intégralité du référentiel : les **24 correspondances exactes** de
`dracaufeu` sont toutes dans le résultat de `charizard`. Les 7 cartes en plus
côté anglais n'ont **aucun nom français** — elles ne peuvent pas être trouvées
par une requête française, et c'est la bonne réponse.

---

## Endpoints

| Méthode | Chemin | Rôle |
|---|---|---|
| `GET` | `/api/v1/cards` | Recherche et parcours, filtré et paginé |
| `GET` | `/api/v1/cards/{id}` | Détail d'une carte, **avec toutes ses impressions** |
| `GET` | `/api/v1/tcgs` | Jeux disponibles |
| `GET` | `/api/v1/expansions` | Extensions, les plus récentes d'abord |
| `GET` | `/api/v1/rarities` | Raretés (valeurs du filtre `rarity`) |
| `GET` | `/api/v1/finishes` | Finitions |
| `GET` | `/api/v1/card-types` | Types de carte |

### `GET /api/v1/cards`

| Paramètre | Défaut | Rôle |
|---|---|---|
| `q` | — | Terme recherché, toutes langues. Minimum 2 caractères |
| `tcg` | — | Code du jeu, ex. `pokemon` |
| `expansion` | — | Code d'extension, ex. `base1` |
| `rarity` | — | Code de rareté, ex. `uncommon` |
| `card_type` | — | Code de type, ex. `pokemon` |
| `language` | — | Restreint la recherche à une langue |
| `page` | `1` | |
| `page_size` | `20` | Maximum 100 |

**Sans `q`**, c'est un parcours filtré, trié par numéro — de quoi afficher une
extension entière.

**Les filtres portent sur les `code`, jamais sur les libellés traduits.**
`rarity=uncommon` fonctionne, `rarity=Peu%20Commune` renvoie zéro résultat.
C'est volontaire : un libellé change avec la langue, un code non. Les valeurs
possibles se récupèrent sur `/rarities`, `/finishes`, `/card-types`.

### Forme d'une réponse

Tout ce qui est traduit sort en **dictionnaire indexé par langue**, jamais
résolu dans une langue choisie par le serveur : c'est le client qui sait quoi
afficher, et une même réponse sert toutes les langues.

```json
{
  "items": [{
    "id": 809, "number": "4", "number_sort": 4,
    "names":  {"fr": "Dracaufeu", "en": "Charizard"},
    "images": {"fr": "https://assets.tcgdex.net/fr/base/base1/4",
               "en": "https://assets.tcgdex.net/en/base/base1/4"},
    "rarity": {"id": 3, "code": "rare", "labels": {"fr": "Rare", "en": "Rare"}},
    "expansion": {"code": "base1",
                  "names": {"fr": "Set de Base", "en": "Base Set"}},
    "score": 1.0
  }],
  "total": 133, "page": 1, "page_size": 20
}
```

`score` n'apparaît que sur une recherche par nom : `1.0` = correspondance
exacte. `total` est le nombre total de résultats, pas le nombre renvoyé —
sans lui le front ne peut pas paginer.

### `GET /api/v1/cards/{id}`

Ajoute `attributes`, `external_ids`, `localizations` et surtout `printings` —
le niveau auquel se rattacheront les prix (lot 5) et les exemplaires possédés
(lot 4) :

```json
"printings": [
  {"language": "en", "finish": {"code": "holo"},
   "external_ids": {"cardmarket": [273699, 660224],
                    "tcgplayer":  [42382, 106999]}},
  {"language": "en", "finish": {"code": "first_edition"}, "external_ids": {}},
  {"language": "fr", "finish": {"code": "holo"},  "…": "…"},
  {"language": "fr", "finish": {"code": "first_edition"}, "external_ids": {}}
]
```

Les listes d'identifiants viennent de la décision du lot 2 : plusieurs
identifiants Cardmarket pour une même finition sont conservés entiers.

`404` si la carte n'existe pas.

---

## Comment la recherche fonctionne

### Trois pièces, un seul index

**1. Le terme est normalisé par PostgreSQL, pas par Python.** La colonne
`card_localization.name_normalized` est générée
(`lower(immutable_unaccent(name))`). Normaliser la requête avec un
`unicodedata` maison finirait par diverger du dictionnaire `unaccent` de la
base, et la recherche raterait des cartes **en silence**. On paie donc un
aller-retour SQL pour normaliser avec *la même fonction*.

**2. Deux opérateurs complémentaires.** `%` (trigram) absorbe les fautes de
frappe ; `LIKE '%…%'` attrape les sous-chaînes que le trigram note trop bas —
« feu » dans « Dracaufeu ». Les deux s'appuient sur le même index GIN
`ix_card_localization_normalized_trgm`, vérifié par `EXPLAIN`.

**3. On cherche des localisations, on renvoie des cartes.** Le `GROUP BY` sur
`card.id` est exactement ce qui fait que « dracaufeu » et « charizard »
retombent sur la même carte.

### Le score

```
score = GREATEST(
    similarity(nom_normalisé, terme),
    CASE exact      → 1.0
         préfixe    → 0.9
         sous-chaîne→ 0.8
         sinon      → 0 END)
```

`GREATEST` plutôt qu'un `CASE` exclusif : une correspondance floue qui
dépasserait ces planchers garde sa valeur au lieu d'être rabotée.

### Les jokers LIKE sont neutralisés

Chercher `dra_aufeu` ne doit pas trouver « dracaufeu » par effet de joker.
L'échappement traite l'antislash **en premier** — sinon il ré-échapperait les
échappements ajoutés juste après.

---

## Ce que la recherche ne fait pas bien

Deux limites mesurées, à connaître avant de s'en étonner :

**Une faute peut classer un voisin plus court devant la bonne carte.**
`q=dracofeu` remonte « Draco » (0,50) avant « Dracaufeu » (0,46). C'est le
verdict du trigram, pas un bug : « draco » partage proportionnellement plus de
trigrammes avec « dracofeu ». `word_similarity` et `strict_word_similarity`
donnent le même ordre. Les deux cartes sortent, l'utilisateur tranche.

**Deux fautes dans un nom court font tomber sous le seuil.** `q=sharizrd` ne
trouve pas Charizard : la similarité passe sous `pg_trgm.similarity_threshold`
(0,3 par défaut). Abaisser le seuil ramènerait beaucoup de bruit — un réglage
à faire avec le front du lot 6, sur des cas réels.

**Sous 3 caractères, l'index trigram ne sert plus.** PostgreSQL retombe sur un
parcours séquentiel. À 45 000 lignes ça reste de l'ordre de la dizaine de
millisecondes, donc on laisse passer plutôt que d'interdire.

---

## Performances

Médianes sur 5 appels, référentiel complet (23 649 cartes, 45 528
localisations) :

| Cas | Médiane |
|---|---|
| Recherche exacte (`dracaufeu`) | 25 ms |
| Recherche avec faute (`dracofeu`) | 25 ms |
| Sous-chaîne large (`feu`, 387 résultats) | 27 ms |
| Parcours d'une extension | 16 ms |
| Parcours sans filtre (23 649 résultats) | 25 ms |
| Page profonde (page 10) | 31 ms |
| Détail d'une carte | 15 ms |

Le chargement des relations passe par `selectinload` : une requête par
relation pour toute la page, pas une par carte. Sans ça, 20 résultats
déclencheraient une centaine d'allers-retours.

La pagination est déterministe — tri secondaire sur `card.id`. Vérifié : 7
pages de 25 sur `pikachu` donnent 175 cartes distinctes, **zéro doublon**, et
la même requête rejouée rend le même ordre.

---

## Exemples

```bash
# Recherche, toutes langues
curl 'localhost:8000/api/v1/cards?q=dracaufeu'

# Tolérante aux fautes
curl 'localhost:8000/api/v1/cards?q=dracofeu'

# Insensible aux accents : les deux donnent le même total
curl 'localhost:8000/api/v1/cards?q=evoli'
curl 'localhost:8000/api/v1/cards?q=%C3%A9voli'

# Une extension entière, dans l'ordre des numéros
curl 'localhost:8000/api/v1/cards?expansion=swsh3&page_size=50'

# Filtres combinés
curl 'localhost:8000/api/v1/cards?q=pikachu&rarity=uncommon&language=fr'

# Détail, avec toutes les impressions
curl 'localhost:8000/api/v1/cards/809'

# Valeurs possibles des filtres
curl 'localhost:8000/api/v1/rarities?tcg=pokemon'
```

---

## Tests

```bash
docker compose exec api python -m pytest tests/ -q     # 29 tests
```

Les tests de l'API ont besoin de la base et du référentiel importé ; ils se
**sautent** proprement si `DATABASE_URL` est absent, plutôt que d'échouer et de
masquer un vrai problème.

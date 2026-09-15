# Gestion de la collection — lot 4

CRUD des exemplaires, ventes, statistiques et complétion. Préfixe `/api/v1`.

---

## Le principe central : on ne regroupe jamais par nom

« Dracaufeu » et « Pikachu » désignent des **dizaines de cartes différentes**.
La clé de regroupement est l'**impression** — donc une carte précise, d'une
extension précise, dans une finition et une langue précises. Le nom
n'intervient nulle part comme identifiant.

Dix Pikachu dont cinq identiques s'affichent ainsi :

```
Carte     Extension                   N°     Finition       Lg   Qté
--------------------------------------------------------------------
Pikachu   Neo Genesis                 70     first_edition  fr     1
Pikachu   Wizards Black Star Promos   4      normal         fr     2
Pikachu   Jungle                      60     first_edition  fr     2
Pikachu   Set de Base                 58     first_edition  fr     5
                                                            total   10
```

Quatre entrées, dont une à 5. Jamais une entrée « Pikachu ×10 ».

---

## Endpoints

| Méthode | Chemin | Rôle |
|---|---|---|
| `GET` | `/collection/items` | Lister, groupé par impression, filtré et paginé |
| `POST` | `/collection/items` | Ajouter des exemplaires |
| `PATCH` | `/collection/items/{id}` | Modifier un lot |
| `DELETE` | `/collection/items/{id}` | Retirer un lot |
| `POST` | `/collection/sales` | Enregistrer une vente |
| `GET` | `/collection/sales` | Historique des ventes |
| `DELETE` | `/collection/sales/{id}` | Annuler une vente |
| `GET` | `/collection/stats` | Statistiques |
| `GET` | `/collection/completion` | Complétion par extension |

Filtres du listing : `q` (nom, toutes langues), `tcg`, `expansion`, `rarity`,
`finish`, `language`, `condition`. Comme au lot 3, ils portent sur les
**codes**, jamais sur les libellés traduits.

---

## Ajouter : incrémenter ou créer un lot

Deux gestes différents, deux comportements :

**Sans prix d'achat** → l'ajout **fusionne** avec une ligne existante de même
impression et même état, et renvoie `200`. C'est le geste courant, celui qui
suit l'ouverture d'un booster.

```bash
curl -X POST localhost:8000/api/v1/collection/items \
  -H 'Content-Type: application/json' \
  -d '{"printing_id": 34730, "quantity": 2}'
# 201 la première fois, puis 200 avec quantity qui monte
```

**Avec un prix d'achat** → un **lot distinct** est créé, et renvoie `201`. Ce
prix est un prix de revient qui lui est propre : le fusionner le perdrait sans
retour.

```bash
curl -X POST localhost:8000/api/v1/collection/items \
  -H 'Content-Type: application/json' \
  -d '{"printing_id": 34730, "quantity": 1,
       "unit_purchase_price": "180.00", "purchase_currency": "EUR",
       "purchase_date": "2026-03-01"}'
```

> `unit_purchase_price` est un **prix unitaire**, pas le prix du lot.
> `quantity: 3, unit_purchase_price: 60` se lit « trois cartes à 60 € ».
> La colonne s'appelait `purchase_price` au lot 1 ; elle a été renommée
> (migration `0006`) parce que l'ambiguïté aurait fini par coûter un bug.

Le prix et sa devise vont **ensemble ou pas du tout** : un montant sans devise
est ininterprétable. Une contrainte `CHECK` le garantit en base, l'API renvoie
`409`.

---

## Vendre

Une vente **retire les exemplaires de la collection** et crée une ligne dans
un registre séparé.

```bash
curl -X POST localhost:8000/api/v1/collection/sales \
  -H 'Content-Type: application/json' \
  -d '{"printing_id": 34839, "quantity": 2, "unit_sale_price": "12.50",
       "sale_date": "2026-09-10", "platform": "Cardmarket"}'
```

Sans `collection_item_id`, les exemplaires sont pris sur les **lots les plus
anciens d'abord** — un ordre arbitraire mais stable, préférable à un choix qui
dépendrait de l'ordre de lecture de la base.

Vendre plus qu'on ne possède renvoie `409` avec le décompte :

```json
{"detail": "Vente impossible : 99 demandé(s), 3 en collection."}
```

**Vendre son dernier exemplaire supprime la ligne de collection, mais pas la
vente.** C'est la raison d'être d'une table séparée : sans elle, le total des
ventes s'effondrerait à chaque fois qu'on solde un stock.

### Pourquoi une table dédiée plutôt que des colonnes `sold_at` / `sale_price`

Deux raisons, chacune suffisante :

1. **Les ventes partielles.** Vendre 2 exemplaires sur 4 ne peut pas se
   représenter par une ligne « à moitié vendue ».
2. **L'historique survit.** `collection_item_id` est nullable et en
   `SET NULL` : la vente reste quand le lot disparaît.

Ce lien n'est d'ailleurs renseigné que si **un seul lot a été touché et qu'il
survit**. Prétendre rattacher une vente étalée sur trois lots à un lot unique
serait faux, et pointer un lot vidé — donc supprimé dans la même transaction —
violerait la clé étrangère.

### La plateforme est normalisée

`sale_platform` est une table de référence, alimentée au fil des ventes. Le
code est normalisé, le libellé garde la saisie :

```
VINTED  →  code "vinted", libellé "VINTED"
vinted  →  même ligne
```

Sans ça, « Vinted », « vinted » et « VINTED » seraient trois plateformes dans
les statistiques.

> À ne pas confondre avec `price_source` (lot 5), qui dit *où on lit une cote*
> et non *où j'ai vendu*. Les deux se recoupent parfois — Cardmarket est les
> deux — mais ne servent pas à la même chose.

### Annuler une vente ne remet rien en collection

`DELETE /collection/sales/{id}` efface la vente, point. Remettre
l'exemplaire supposerait de savoir dans quel lot — ce qu'on ne sait pas.
Réajouter la carte est un geste distinct, et explicite.

---

## Statistiques

### Trois compteurs, pas un

« Combien de cartes ai-je ? » n'a pas une réponse mais trois, et l'écart est
énorme sur un référentiel où 14 044 cartes ont deux impressions et 8 356 en
ont quatre :

| Champ | Question | 2 Dracaufeu holo FR + 1 holo EN |
|---|---|---|
| `items` | Combien de cartes physiques | **3** |
| `distinct_printings` | Combien de versions différentes | **2** |
| `distinct_cards` | Combien de cartes du référentiel | **1** |

C'est la réponse à « je veux voir ma collection avec et sans les doublons » :
les trois cohabitent, et c'est l'affichage qui choisit.

### L'argent n'est jamais converti

```json
"purchase_value": [{"currency": "EUR", "total": "180.00", "quantity": 1}],
"items_without_price": 8,
"sales_total":    [{"currency": "EUR", "total": "52.00", "quantity": 5}],
"sales_by_platform": [
  {"platform": "Cardmarket", "currency": "EUR", "total": "25.00", "quantity": 2},
  {"platform": "VINTED",     "currency": "EUR", "total": "17.00", "quantity": 2}
]
```

Chaque total est rendu **par devise**. Additionner des euros et des dollars
avec un taux inventé produirait un chiffre faux et crédible — le pire des deux
mondes. Le jour où plusieurs devises seront courantes, la conversion se fera
avec un taux réel, daté, et affiché comme tel.

`items_without_price` compte les exemplaires dont le prix n'a jamais été
saisi. **Ils ne valent pas zéro** : leur prix est inconnu, ce qui est une
information différente et qu'on rend explicite plutôt que de la noyer.

---

## Complétion

**Une carte compte dès qu'on en possède une impression, quelle que soit sa
langue.** Décision du 15/09/2026 : une Charizard anglaise complète le set au
même titre que sa version française.

```bash
curl 'localhost:8000/api/v1/collection/completion?tcg=pokemon'
```

```
Extension                    possédées  officiel   total    %off    %tot
Set de Base                          1       102     102     1.0     1.0
```

**Deux dénominateurs**, parce qu'ils racontent deux choses : `official` est le
total imprimé sur la carte (le 102 de « 4/102 »), `total` inclut les cartes
secrètes. Pour l'extension *151*, c'est `165` contre `207` — et « set
complet » ne veut pas dire la même chose dans les deux cas.

Le paramètre `language` restreint à une langue, pour qui vise un set dans une
seule langue :

```bash
curl 'localhost:8000/api/v1/collection/completion?language=fr'
```

Par défaut, seules les extensions dont on possède au moins une carte sont
renvoyées — sinon la réponse ferait 221 lignes à zéro. `include_empty=true`
les inclut toutes.

---

## Ce que le lot 4 ne fait pas

- **La valorisation au prix du marché** : c'est le lot 5. Ici on ne parle que
  de ce qui a été **payé** et **encaissé**.
- **La plus-value** : écartée, elle se lit d'elle-même sur les deux totaux.
- **Les cartes gradées** (PSA, BGS) : axe distinct de `condition`, pas au
  schéma.
- **Les frais de port et commissions** : non demandés ; ils fausseraient le
  total des ventes si on les ajoutait à moitié.
- **Le multi-utilisateur** : hors périmètre. Une seule collection implicite.

---

## Tests

```bash
docker compose exec api python -m pytest tests/ -q     # 43 tests
```

Les tests de la collection écrivent dans la base de développement, qui
contiendra un jour la **vraie** collection. Ils ne vident donc jamais rien :
chaque test crée ce dont il a besoin, raisonne en **écarts** plutôt qu'en
valeurs absolues, et nettoie ses propres lignes.

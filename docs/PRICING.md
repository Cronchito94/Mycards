# Prix et valorisation — lot 5

Relevé quotidien des cotes, historique construit par nous, et valorisation de
la collection au prix du marché.

---

## La source : TCGdex sert aussi les prix

**Mesuré le 15/09/2026**, sur un réseau sans inspection TLS. Le serveur TCGdex
qu'on auto-héberge depuis le lot 2 embarque ses propres connecteurs Cardmarket
et TCGplayer, et sert leurs cotes sur chaque carte :

```
cardmarket : trend 0,09 €   avg1 0,06   avg7 0,10   avg30 0,09
             trend-holo 0,19 €   avg1-holo 0,35   avg7-holo 0,22
tcgplayer  : normal marketPrice 0,21 $   reverse-holofoil 0,46 $
```

**Sans clé d'API, sans compte, sans quota.** C'est ce qui a rendu inutile une
demande d'accès Cardmarket — dont l'API n'accepte de toute façon plus de
nouvelles inscriptions.

### Activer les prix

Désactivés par défaut : le démarrage passe de ~25 s à **5-10 minutes**, et
l'import du lot 2 n'en a pas besoin. Dans le `.env` :

```bash
TCGDEX_CI=false
TCGCSV_USER_AGENT=mycards/0.1 mon.email@exemple.fr
```

> ⚠️ **`TCGCSV_USER_AGENT` est obligatoire dès que `TCGDEX_CI=false`.**
> Sans elle, le chargement TCGplayer lève une exception qui interrompt la
> séquence de démarrage. Le serveur affiche alors « 🚀 Server ready » — **et
> n'écoute jamais**. Toute requête répond `Empty reply from server`.
>
> C'est ce qui avait été pris pour un effet de l'inspection TLS au lot 2.
> C'était faux : `CI=true` n'est pas une parade à WARP, c'est un contournement
> de cette variable manquante.

Le `healthcheck` du service a un `start_period` de 600 s pour cette raison :
plus court, il déclarerait malade un conteneur qui travaille.

---

## Le piège du mapping, mesuré et non supposé

Les entrées `normal` et `reverse` d'une même carte portent un bloc `cardmarket`
**identique**, avec le même `idProduct`. Cardmarket ne sépare pas ses cotes par
variante : la reverse se lit dans les champs **suffixés `-holo`** du même bloc.

| Notre finition | Champ Cardmarket | Sur `swsh3-136` |
|---|---|---|
| `normal` | `trend` | 0,09 € |
| `reverse` | `trend-holo` | 0,19 € |

Appliquer `trend` aux deux sous-évaluerait **toutes les reverse de moitié**.
TCGplayer, lui, sépare proprement par clé de variante (`normal`,
`reverse-holofoil`) — deux sources, deux logiques.

### Ce qui reste à valider

La règle ci-dessus est vérifiée sur une carte qui existe en normal + reverse.
Deux cas n'ont pas pu l'être faute de données :

- **Une carte holo sans version normale.** Le produit Cardmarket *est* alors la
  holo, donc `trend` devrait être correct — c'est l'hypothèse retenue.
- **`first_edition`.** Ces impressions n'ont aucun identifiant Cardmarket dans
  notre base : elles remontent en « sans cote », ce qui est le comportement
  voulu.

Le mode `--dry-run` existe pour trancher ces cas sur de vraies cartes.

---

## Le job de relevé

```bash
# Simulation : interroge la source, n'écrit rien
docker compose exec api python -m app.pricing.cli --dry-run

# Relevé réel
docker compose exec api python -m app.pricing.cli
```

**Il ne relève que les impressions possédées ou surveillées**, jamais
l'intégralité du référentiel — 64 301 impressions contre quelques centaines
réellement concernées. Ce n'est pas une optimisation : c'est ce qui rend le
job tenable et poli envers la source. C'est aussi la raison d'être de
`watched_printing`, posée au lot 1.

La métrique retenue est **`trend`**, décision du 15/09/2026 : c'est
l'estimation lissée de Cardmarket, celle que les collectionneurs regardent.
`avg1` serait plus proche de « dernière vente » mais saute d'un jour à l'autre
(0,06 contre 0,09 sur la même carte). Le bloc complet est conservé dans
`price_snapshot.raw` : changer d'avis ne demandera pas de réinterroger la
source, l'historique est rejouable.

Le job est **rejouable** : le relancer le même jour met à jour, ne duplique
pas. Il renvoie un code de sortie non nul si rien n'a été relevé alors que des
impressions étaient visées — un ordonnanceur peut ainsi s'en apercevoir.

---

## Valorisation

```bash
curl 'localhost:8000/api/v1/collection/valuation'
curl 'localhost:8000/api/v1/collection/valuation?at=2026-09-08'
curl 'localhost:8000/api/v1/collection/valuation/history?days=30'
```

La valeur de marché apparaît aussi dans `/collection/stats`, pour qu'un tableau
de bord tienne en un seul appel.

### Les quatre règles

**1. La cote NM s'applique à tous les états.** Sorti de booster, une carte est
NM ; les autres états ne viennent que d'achats. C'est le choix de Collectr.
Contrepartie assumée : sur une carte vintage, une GD vaut souvent 20 à 30 %
d'une NM — la valeur est donc *surévaluée* sur ces cartes-là.

**2. L'euro fait référence**, via Cardmarket. Les montants restent rendus par
devise : **aucune conversion, jamais**.

**3. La cote la plus récente**, sans limite d'ancienneté imposée. `oldest_quote`
dit sur quelles dates s'appuie le total — c'est au lecteur de juger si c'est
frais, pas au serveur de décider en silence.

**4. Les impressions sans cote sont comptées à part.** Elles ne valent pas
zéro : leur valeur est **inconnue**, ce qui est une information différente.
Les noyer dans le total donnerait un chiffre faux et crédible.

### Vérification du calcul

Avec 3 exemplaires en normal et 2 en reverse de `swsh3-136`, aux cotes réelles :

```
15/09 : 3 × 0,09 + 2 × 0,19 = 0,65 €
08/09 : 3 × 0,07 + 2 × 0,22 = 0,65 €
01/09 : 3 × 0,11 + 2 × 0,17 = 0,67 €
```

La série temporelle rend bien `0,67 → 0,65 → 0,65`.

### ⚠️ Ce que la courbe dit, et ne dit pas

Elle applique les cotes passées à la collection **d'aujourd'hui**. Elle répond
donc à « combien vaudrait ma collection actuelle aux prix d'alors », **pas** à
« combien valait ma collection alors ».

Reconstituer la seconde exigerait un historique des possessions que le schéma
ne garde pas : on connaît la date d'ajout d'un lot, mais une suppression ne
laisse aucune trace. Ce serait un autre lot, et un autre modèle.

---

## Conditions d'utilisation : ce qui n'a PAS pu être vérifié

La règle du projet impose de vérifier les CGU avant tout appel à une source
tierce. Cette fois, **je n'ai pas pu**.

`downloads.s3.cardmarket.com`, `tcgcsv.com` et `help.cardmarket.com` sont
inaccessibles depuis l'environnement où ce code a été écrit — refus de
politique réseau, pas panne. Ce qui a été établi :

- **TCGdex est en MIT**, base de données comprise (vérifié au lot 2).
- Que TCGdex redistribue ces cotes **ne dit pas** ce que nous avons le droit
  d'en faire.

**Reste à vérifier avant toute ouverture publique de l'application** :

1. Les conditions du *price guide* Cardmarket
   (`downloads.s3.cardmarket.com/productCatalog/priceGuide/price_guide_6.json`),
   qui est un fichier public mais pas nécessairement libre de réutilisation.
2. Celles de **TCGCSV**, qui impose déjà un `User-Agent` identifiant — signe
   qu'un cadre d'usage existe.
3. La distinction **usage personnel / application publique** : afficher des
   cotes Cardmarket à des tiers est une redistribution de données.

Tant que l'application reste personnelle, le risque est faible. Il change de
nature dès l'ouverture au public en Europe.

---

## Architecture : le connecteur est remplaçable

`app/pricing/base.py` définit une interface abstraite, conformément à la spec.
Ce n'est pas de la précaution théorique : `docs/SPEC.md` désignait
pokemontcg.io comme source, qui s'est dégradée depuis ; Cardmarket a fermé son
API aux nouvelles demandes. Une source de prix est une pièce **remplaçable**.

Un connecteur ne fait qu'une chose : pour un lot d'impressions, renvoyer les
cotes qu'il connaît. Il ne décide pas de ce qu'on relève, n'écrit rien en base,
et ne choisit pas la métrique canonique.

Une seconde implémentation est identifiée mais non écrite : **lire directement
le dump Cardmarket**, un simple JSON public découvert dans les logs de TCGdex.
Elle supprimerait la dépendance au démarrage fragile du serveur TCGdex. Elle
demande d'inspecter la structure du fichier, ce qui n'a pas été possible ici.

---

## Tests

```bash
docker compose exec api python -m pytest tests/ -q     # 53 tests
```

Le mapping des finitions se teste **sans base ni réseau**, à partir d'un
extrait réel de la réponse TCGdex. Les tests de valorisation, eux, ont besoin
de la base : ils créent ce dont ils ont besoin et l'annulent ensuite.

> Détail qui a son importance : un ajout sans prix **fusionne** avec une ligne
> existante. Le harnais de test distingue donc une ligne qu'il a créée — à
> supprimer — d'une ligne qu'il a rejointe — à ramener à sa quantité d'avant.
> Sans cette distinction, un test aurait effacé des exemplaires qu'il n'avait
> pas créés.

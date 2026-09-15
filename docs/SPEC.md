# Prompt de cadrage — Gestionnaire de collection TCG

> À coller dans Claude Code au démarrage du projet.
> Il sert aussi de spec : garde-le dans le repo (`docs/SPEC.md`) et fais-le évoluer.

---

## Contexte

Je construis une application de gestion de collection de cartes TCG (web + mobile), pour remplacer le fait de devoir jongler entre plusieurs applis.

**Deux jeux sont visés, et deux seulement : Pokémon et Riftbound (League of Legends).** Ce sont les deux que je collectionne. Le modèle de données reste **multi-TCG dès le départ** — c'est ce qui permet d'accueillir le second sans tout reprendre — mais aucun autre jeu n'est au programme, et rien ne doit être fait « au cas où » pour Magic ou One Piece.

Pokémon est le premier servi : son référentiel est mûr, multilingue, et sert de banc d'essai. Riftbound suit immédiatement, avant que l'application ne se soit installée dans des réflexes mono-jeu.

Fonctionnalités visées à terme :

- Collection séparée par TCG
- Recherche de carte par nom, en français ou en anglais (et autres langues à terme)
- Valorisation de la collection avec variation sur plusieurs jours / semaines / mois / années
- Comparatif de prix entre plusieurs plateformes
- Ajout d'une carte par photo : l'app identifie la carte, ou propose les plus proches, avec recherche manuelle en secours

## Stack imposée

- **Backend** : Python 3.12, FastAPI, SQLAlchemy 2.x, Alembic pour les migrations
- **Base** : PostgreSQL (extension `pg_trgm` activée, pour la recherche floue)
- **Conteneurisation** : `docker-compose.yml` à la racine, avec Postgres, dès le lot 0. Tout doit tourner avec un `docker compose up`.
- **Front** : PWA (pas d'application native). On décidera du framework au lot 6, pas avant.
- **Déploiement cible** : un serveur perso sous Docker, plus tard. Ne fais aucune hypothèse sur un hébergeur cloud.

## Règles de travail

1. **Travaille lot par lot. À la fin de chaque lot, tu t'arrêtes et tu me montres le résultat.** Ne démarre jamais le lot suivant sans mon accord explicite.
2. Avant d'ajouter une dépendance qui n'est pas dans la stack ci-dessus, demande-moi.
3. **Pas de données inventées.** Si une API ne renvoie pas ce qu'on attend, dis-le-moi, ne bouche pas le trou avec des mocks ou des fixtures fantaisistes.
4. Secrets et tokens dans un `.env` gitignoré, avec un `.env.example` commité.
5. **Ne commit jamais d'images de cartes** (copyright) ni de dump de la base de référence. On stocke des URLs et on fournit un script d'import.
6. Écris un `CLAUDE.md` à la racine au lot 0 et tiens-le à jour : commandes utiles, conventions, état d'avancement.
7. Commits atomiques et messages explicites. Une branche par lot.
8. Explique-moi tes choix d'architecture quand ils ne sont pas évidents — je veux comprendre le code, pas juste le recevoir.

---

## Découpage en lots

### Lot 0 — Fondations

Squelette du projet : arborescence, `docker-compose.yml` (Postgres + API), Dockerfile, FastAPI qui démarre avec un `/health`, Alembic configuré, `.env.example`, `.gitignore`, `README.md`, `CLAUDE.md`.

**Critère de fin** : `docker compose up` démarre, `/health` répond, la base est joignable.

---

### Lot 1 — Schéma de données

C'est le lot le plus important. Le modèle doit séparer quatre niveaux distincts :

- `TCG` — le jeu (Pokémon, Riftbound…)
- `EXTENSION` — le set / la série
- `CARTE` — la carte au sens abstrait : son numéro dans l'extension, sa rareté, son illustration
- `NOM_LOCALISE` — un nom par langue, rattaché à la carte (c'est ce qui permet la recherche FR/EN)
- `IMPRESSION` — la déclinaison concrète : finition (normale, reverse holo, holo, 1st edition…) et langue. **C'est le niveau auquel s'attache un prix.**
- `EXEMPLAIRE` — ce que je possède réellement : quantité, état (NM, EX, GD…), prix et date d'achat
- `PRIX_SNAPSHOT` — un relevé de prix daté, par impression et par source

Règles :

- Un prix ne s'attache jamais à une `CARTE`, toujours à une `IMPRESSION`. Une reverse holo et une normale n'ont pas la même valeur.
- Prévois sur `EXTENSION` et `CARTE` un champ de correspondance vers les identifiants externes (TCGdex, pokemontcg.io), sous forme de JSONB, pour croiser les sources.
- Index trigram sur `NOM_LOCALISE.nom` pour la recherche floue.
- Tous les montants en `NUMERIC`, jamais en float, avec la devise stockée.

Livrable : modèles SQLAlchemy + migration Alembic initiale + un schéma commenté dans `docs/`.

**Critère de fin** : la migration s'applique, et je valide le modèle avant qu'on aille plus loin.

---

### Lot 2 — Import du référentiel Pokémon

Script d'import idempotent (relançable sans dupliquer) depuis **TCGdex**, qui est multilingue nativement — c'est notre source de vérité pour les cartes, extensions, noms FR/EN et URLs d'images.

À gérer : pagination, rate limiting, reprise après interruption, un rapport de fin (nombre d'extensions, cartes, noms importés, erreurs).

Le mapping des finitions vers nos `IMPRESSION` est le point délicat : commence par explorer ce que renvoie réellement l'API et **montre-moi la structure des données avant d'écrire le mapping**.

**Critère de fin** : la base contient l'intégralité du référentiel Pokémon FR + EN, et je peux le vérifier en SQL.

---

### Lot 2 bis — Import du référentiel Riftbound

**Placé ici volontairement, avant la recherche.** Le second jeu doit entrer en base pendant que le modèle est encore malléable : si la recherche, la collection et la valorisation sont écrites en ne voyant que du Pokémon, elles finiront mono-jeu sans que personne ne s'en aperçoive. Deux jeux en base, c'est le seul test honnête du « multi-TCG ».

Ce que l'exploration du 15/09/2026 a établi :

- **670 cartes** sur trois extensions (Origins 349, Spiritforged 297, Proving Grounds 24) dans le jeu de données communautaire `apitcg/riftbound-tcg-data` — mais il s'arrête à juillet 2026 et ne contient **pas** l'extension *Unleashed*.
- Ce jeu de données est un **dump du catalogue produits TCGplayer** : il mélange cartes et produits scellés (boosters, displays), reconnaissables à leur `cardType` nul. À filtrer.
- Il porte en revanche un `tcgplayer.id` par carte — une correspondance directe vers une source de prix, à stocker dans `external_ids`.
- **Aucune source communautaire ne fournit le français**, alors que le français officiel existe depuis le 29/05/2026 (troisième langue du jeu). La seule source de noms FR est la galerie officielle Riot.
- Autres pistes : Riftcodex (`https://api.riftcodex.com`, REST ouvert sans clé, projet de fans non affilié à Riot) et Scrydex (payant).

Le travail du lot consiste donc d'abord à **trancher la source**, et à me montrer la structure réelle avant tout mapping — comme au lot 2. Le point dur n'est pas l'import : c'est le français.

**Critère de fin** : les cartes Riftbound sont en base à côté des cartes Pokémon, sans migration ni traitement particulier au jeu, et je peux le vérifier en SQL.

---

### Lot 3 — API de recherche

Endpoints REST : recherche de carte par nom (toutes langues confondues, insensible à la casse et aux accents, tolérante aux fautes via trigram), filtres par extension / rareté / TCG, pagination, et récupération du détail d'une carte avec toutes ses impressions.

**Critère de fin** : je cherche « dracaufeu » ou « charizard » et j'obtiens les mêmes cartes.

---

### Lot 4 — Gestion de la collection

CRUD sur les `EXEMPLAIRE` : ajouter une carte à ma collection en choisissant l'impression, la quantité, l'état, le prix d'achat. Endpoints de listing avec filtres, et statistiques simples (nombre de cartes, complétion par extension).

**Critère de fin** : je peux constituer une collection via l'API et la consulter.

---

### Lot 5 — Prix et historique

Point clé : **aucune API ne fournit d'historique de prix, c'est nous qui le construisons.** D'où la table de snapshots.

- Récupération des prix via **pokemontcg.io**, qui redistribue des relevés Cardmarket et TCGplayer mis à jour quotidiennement. Vérifie l'état actuel de cette API avant de coder.
- Job planifié quotidien qui ne relève les prix **que des impressions que je possède ou que je surveille** — surtout pas l'intégralité du référentiel.
- Endpoints : valorisation de la collection à date, et série temporelle de variation sur 7j / 30j / 1an.
- Architecture le connecteur de prix derrière une interface abstraite : on ajoutera d'autres sources.

**Critère de fin** : le job tourne, remplit les snapshots, et je peux sortir la valeur de ma collection sur une période.

---

### Lot 6 — Front PWA

Là seulement on fait l'interface. Installable, responsive, accès caméra prévu pour le lot suivant. Écrans : recherche, détail de carte, ma collection, valorisation avec graphe d'évolution.

Propose-moi deux options de framework avec leurs compromis avant de choisir.

---

### Lot 7 — Reconnaissance de carte par photo

**Ne tente surtout pas d'entraîner un modèle de classification** — des dizaines de milliers de classes, à réentraîner à chaque extension, c'est une impasse.

Pipeline en cascade, du plus fiable au moins fiable :

1. **Prétraitement** : détection des bords de la carte et redressement de la perspective (OpenCV, `findContours` + `warpPerspective`). C'est 90 % du taux de réussite — soigne cette étape en priorité.
2. **OCR du numéro de carte** (le `025/165` en bas). Numéro + extension identifient une carte de façon unique : c'est la clé la plus discriminante.
3. **OCR du nom**, croisé avec `NOM_LOCALISE` en recherche trigram pour absorber les erreurs de lecture.
4. **Hash perceptuel de l'illustration** : pHash précalculé sur toutes les images du référentiel, comparaison par distance de Hamming.
5. **Recherche manuelle** en dernier recours.

L'API renvoie une liste de candidats classés par score de confiance, jamais une réponse unique imposée. C'est moi qui confirme.

---

## Hors périmètre pour l'instant

- **Tout TCG autre que Pokémon et Riftbound** (Magic, One Piece, Lorcana…). Le schéma doit pouvoir les accueillir sans migration douloureuse, mais on n'écrit pas une ligne pour eux et on ne choisit pas une source en pensant à eux.
- Authentification multi-utilisateurs
- Fonctions sociales, échanges, wishlist partagée

## Point de vigilance

Avant d'écrire le moindre appel vers une API tierce (TCGdex, pokemontcg.io, Cardmarket), vérifie ses conditions d'utilisation et signale-moi tout ce qui restreint l'usage ou la redistribution des données.

---

**Commence par le lot 0 et arrête-toi là.**

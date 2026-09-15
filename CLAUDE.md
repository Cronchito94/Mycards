# CLAUDE.md — conventions et état du projet

Fichier de travail pour Claude Code. **À tenir à jour à chaque fin de lot.**
La spec de référence est dans `docs/SPEC.md`.

---

## État d'avancement

| Lot | Sujet | État |
|---|---|---|
| 0 | Fondations (compose, FastAPI, Alembic) | ✅ terminé |
| 1 | Schéma de données multi-TCG | ✅ terminé |
| 2 | Import référentiel Pokémon (TCGdex) | ⏳ à faire |
| 2 bis | Import référentiel Riftbound | ⏳ à faire |
| 3 | API de recherche | ⏳ à faire |
| 4 | Gestion de la collection | ⏳ à faire |
| 5 | Prix et historique | ⏳ à faire |
| 6 | Front PWA | ⏳ à faire |
| 7 | Reconnaissance par photo | ⏳ à faire |

**Règle de travail : un lot à la fois. On s'arrête en fin de lot et on attend
une validation explicite avant de démarrer le suivant.**

Lots 0 et 1 rejoués et vérifiés le 15/09/2026 sur le poste Windows (Docker
29.7) : la pile démarre, `/health` répond 200, `alembic current` donne
`0003 (head)`, les 12 tables sont créées, et `pg_trgm`, `unaccent` et l'index
`ix_card_name_normalized_trgm` sont bien en place. Une correction a été
nécessaire pour y parvenir — voir « Fins de ligne forcées en LF » plus bas.

---

## Commandes utiles

```bash
# Cycle de vie
docker compose up --build          # démarrer (build + migrations + API)
docker compose up -d               # en arrière-plan
docker compose down                # arrêter
docker compose down -v             # arrêter ET effacer la base (destructif)
docker compose logs -f api         # suivre les logs de l'API
docker compose ps                  # état et santé des conteneurs

# Base de données
docker compose exec db psql -U tcg -d tcg
docker compose exec db psql -U tcg -d tcg -c "SELECT extname FROM pg_extension;"

# Migrations (toujours depuis le conteneur api, qui a le bon DATABASE_URL)
docker compose exec api alembic current
docker compose exec api alembic history
docker compose exec api alembic upgrade head
docker compose exec api alembic downgrade -1
docker compose exec api alembic revision --autogenerate -m "message"

# Vérifications
curl -s http://localhost:8000/health
docker compose exec db psql -U tcg -d tcg -c "\\dt"

# Lint — ruff n'est pas dans l'image runtime (volontairement : elle reste
# minimale). On le lance depuis backend/, hors conteneur.
cd backend && pip install -e ".[dev]" && ruff check app
```

---

## Conventions

### Code

- Python 3.12, `ruff` (ligne à 100), imports triés.
- **Docstrings et commentaires en français**, noms de symboles en anglais.
- Un commentaire explique *pourquoi*, pas *quoi*.
- Typage systématique sur les signatures publiques.

### Base de données

- Tous les montants en `NUMERIC`, **jamais** en `float`, avec la devise à côté.
- Un prix s'attache à une **IMPRESSION**, jamais à une **CARTE**.
- Les identifiants de sources externes (TCGdex, pokemontcg.io) vivent dans une
  colonne `JSONB` de correspondance sur `EXTENSION` et `CARTE`.
- Aucune migration écrite à la main quand l'autogénération suffit — mais on
  **relit toujours** le fichier généré avant de le committer.

### Git

- Commits atomiques, messages explicites en français, format
  `lot N: verbe à l'infinitif ...`.
- Une branche par lot.

### Ce qu'on ne committe jamais

- Le `.env` (seul `.env.example` est versionné).
- Les **images de cartes** (copyright éditeur) — on ne stocke que des URLs.
- Les **dumps du référentiel** — il se reconstruit via le script d'import.
- Toute donnée inventée : si une API ne renvoie pas ce qu'on attend, on le
  signale, on ne bouche pas le trou avec des fixtures fantaisistes.

### Avant tout appel à une API tierce

Vérifier les conditions d'utilisation (TCGdex, pokemontcg.io, Cardmarket) et
signaler toute restriction d'usage ou de redistribution. **Pas encore fait —
à traiter au début du lot 2.**

---

## Décisions d'architecture

### Un seul driver PostgreSQL : psycopg v3

L'application est async (FastAPI + `create_async_engine`), Alembic est sync.
psycopg v3 sert les deux modes derrière le même dialecte SQLAlchemy
(`postgresql+psycopg`), donc **un seul DSN et un seul driver** à gérer, au lieu
du couple classique asyncpg + psycopg2.

### Les extensions Postgres sont posées par une migration

`pg_trgm` et `unaccent` sont créées dans la migration `0001`, pas dans un script
`docker-entrypoint-initdb.d`. Un script d'init ne s'exécute que sur un volume
vierge : il aurait marché en local et silencieusement échoué sur le serveur de
déploiement. Une migration s'applique partout.

### `/health` renvoie 503 si la base est injoignable

Une sonde qui répond 200 alors que Postgres est tombé n'a aucune valeur pour un
orchestrateur. L'endpoint fait un `SELECT 1` et dégrade son code HTTP.

### Le DSN n'est jamais dans `alembic.ini`

`sqlalchemy.url` est laissé vide ; `alembic/env.py` lit l'URL depuis les
settings, donc depuis l'environnement. Rien de secret à committer.

### `backend/` séparé de la racine

Le front PWA du lot 6 arrivera dans un `frontend/` à côté. Le
`docker-compose.yml` reste à la racine et orchestre les deux.

### `card_name.name_normalized` est une colonne générée

La recherche du lot 3 doit ignorer casse et accents. Plutôt que de remplir un
champ normalisé côté Python — qui finit toujours par diverger quand quelqu'un
écrit par un autre chemin — la colonne est `GENERATED ALWAYS AS
(lower(immutable_unaccent(name))) STORED`, et l'index GIN trigram porte sur
elle. `immutable_unaccent` (migration `0002`) est un wrapper `IMMUTABLE` autour
d'`unaccent`, que PostgreSQL déclare seulement `STABLE` ; la contrepartie est
documentée dans `docs/SCHEMA.md`.

### Tables de référence plutôt qu'`ENUM`, sauf pour l'état

`rarity`, `finish` et `price_source` sont des tables : leur vocabulaire est
ouvert, propre à chaque jeu, et inconnu jusqu'au lot 2. Une nouvelle finition
doit s'ajouter par un `INSERT`, pas par une migration.

`condition` est en revanche un `ENUM` PostgreSQL : l'échelle Cardmarket (`MT`,
`NM`, `EX`, `GD`, `LP`, `PL`, `PO`) est fermée et stable.

### Cascades : la collection résiste, le reste s'efface

`card_name`, `printing` et `price_snapshot` sont en `CASCADE` — données
dérivées du référentiel. `collection_item` est en `RESTRICT` : c'est la seule
donnée non reconstructible, un réimport qui la menacerait doit échouer
bruyamment plutôt que l'effacer en silence.

### Dockerfile : stub `app/` avant le `pip install`

Les dépendances s'installent avant la copie du code pour garder le layer en
cache. Comme setuptools a besoin que le paquet existe, on pose un `app/`
vide puis on le supprime ; `PYTHONPATH=/srv/app` garantit que c'est bien le
code réellement copié qui est importé, pas le résidu en `site-packages`.

---

### Fins de ligne forcées en LF (`.gitattributes`)

Git for Windows checkoute en CRLF par défaut (`core.autocrlf=true`).
`docker-entrypoint.sh` partait alors en CRLF dans l'image et le conteneur
mourait sur son shebang — `env: 'bash\r': No such file or directory`, exit 127,
redémarrage en boucle — sans que rien dans les logs ne désigne Git.
`.gitattributes` force `eol=lf` sur tout le texte.

Attention : le fichier corrige les checkouts **futurs**. Les fichiers déjà
présents dans le répertoire de travail restent en CRLF jusqu'à un re-checkout :

```bash
rm backend/docker-entrypoint.sh && git checkout -- backend/docker-entrypoint.sh
```

---

## Points ouverts

### Lot 2 — TCGdex

- Explorer et **montrer la structure réelle** renvoyée par TCGdex avant d'écrire
  le moindre mapping. Les finitions réelles iront dans la table `finish` (un
  `INSERT`, pas une migration) ; `rarity` se remplit de la même façon, au fil de
  l'import.
- L'idempotence de l'import s'appuie sur `external_ids` (JSONB indexé GIN) sur
  `expansion`, `card` et `printing`.
- **TCGdex peut être auto-hébergé**, et c'est probablement le bon choix : image
  `tcgdex/server:edge` (MIT, ~81 Mo, amd64 + arm64), données embarquées, aucune
  base externe. Import sans limite de débit ni dépendance réseau. Vérifié le
  15/09/2026 : démarrage en ~25 s, `/v2/fr/sets` renvoie 200 extensions en
  français. À trancher au lot 2 ; sinon `https://api.tcgdex.net/v2`.
- Si on l'auto-héberge, **`CI=true` est obligatoire** sur le conteneur. Sans
  cette variable, le serveur télécharge les tarifs Cardmarket et TCGplayer
  *avant* d'ouvrir son port HTTP ; derrière une inspection TLS (WARP, proxy
  d'entreprise) ces appels échouent en boucle (`SELF_SIGNED_CERT_IN_CHAIN`) et
  le port 3000 n'est jamais ouvert. Le conteneur paraît sain, les logs affichent
  même « 🚀 Server ready », mais `/proc/net/tcp` ne montre aucun socket en
  écoute. `CI=true` saute ce chargement : c'est l'échappatoire prévue en amont
  (`server/src/index.ts`), et les prix nous concernent au lot 5, pas ici.
- L'amont ne publie **aucun tag versionné** (seulement `edge` et
  `branch-master`) : revérifier que l'API répond après chaque `pull`.

### Lot 3

- Vérifier que l'index `ix_card_name_normalized_trgm` est réellement utilisé
  (`EXPLAIN ANALYZE`) une fois le référentiel chargé — sur une table quasi vide,
  PostgreSQL l'ignore à raison.

### Lot 5 — les sources de prix ont changé depuis la rédaction de la spec

État vérifié le 15/09/2026. `docs/SPEC.md` désigne encore pokemontcg.io comme
source du lot 5 : **à rediscuter avant de coder.**

- **pokemontcg.io a été absorbée par Scrydex** et s'est fortement dégradée
  (mesures publiques : ~59 % d'erreurs, ~8 s de latence moyenne). Ne pas bâtir
  le lot 5 dessus sans l'avoir remesurée.
- **Cardmarket n'accepte plus de nouvelles demandes d'accès à son API.** La voie
  directe est fermée.
- Alternatives : **JustTCG** (palier gratuit 1 000 appels/mois, 100/jour, 17
  jeux dont Riftbound) et **Scrydex** (couverture complète, historique et prix
  gradués, mais 29 $/mois minimum, sans palier gratuit ; 5 000 crédits ≈ 160
  requêtes/jour, l'historique en coûte 3).
- Piste à vérifier au déploiement : le serveur TCGdex embarque ses **propres**
  providers Cardmarket et TCGplayer (visible dans ses logs de démarrage). Hors
  inspection TLS, il pourrait donc servir aussi des prix. Non vérifiable depuis
  le poste, dont le réseau casse ces appels.
- Pour Magic, le jour venu : **Scryfall** publie un dump quotidien gratuit, sans
  clé, prix EUR/USD inclus.
- Le connecteur de prix reste derrière une interface abstraite, conformément à
  la spec — c'est précisément ce qui permet d'absorber ces changements.

### Lot 2 bis — Riftbound

Riftbound n'est plus repoussé après le lot 5 : c'est, avec Pokémon, l'un des
**deux** jeux du projet (`docs/SPEC.md` mis à jour le 15/09/2026). Il s'importe
juste après Pokémon, avant la recherche — deux jeux en base sont le seul test
honnête du caractère multi-TCG.

Exploration du 15/09/2026, à reprendre au moment du lot :

- `apitcg/riftbound-tcg-data` (GitHub, dumps JSON) : **670 cartes réelles** sur
  trois extensions — Origins 349, Spiritforged 297, Proving Grounds 24.
- ⚠️ C'est un **dump du catalogue produits TCGplayer**, pas un référentiel de
  cartes : boosters et displays y côtoient les cartes, reconnaissables à leur
  `cardType` nul (11 sur 360 pour Origins). À filtrer à l'import.
- Champs utiles : `number` (`001/298`), `rarity`, `cardType`, `domain`,
  `energyCost`, `powerCost`, `might`, `description`. Images servies par le CDN
  TCGplayer — on ne stocke que l'URL.
- `tcgplayer.id` par carte : correspondance directe vers une source de prix, à
  ranger dans `external_ids`.
- ⚠️ Dernière mise à jour en juillet 2026 : l'extension **Unleashed** manque
  déjà. Vérifier la fraîcheur avant de s'appuyer dessus.
- ⚠️ **Aucune source communautaire ne fournit le français** (`cards/en/`
  seulement), alors que le français officiel existe depuis le **29/05/2026** —
  troisième langue du jeu, après l'anglais et le chinois simplifié. La seule
  source de noms FR identifiée est la galerie officielle Riot
  (`playriftbound.com/fr-fr/card-gallery/`, dont la version FR existe bien).
  **C'est le point dur du lot**, pas l'import : vérifier les CGU de Riot avant
  d'envisager quoi que ce soit là-dessus.
- Autres pistes : Riftcodex (`https://api.riftcodex.com`, REST ouvert sans clé,
  projet de fans non affilié à Riot) et Scrydex (couvre Riftbound, payant).
- Depuis le poste de référence, `api.apitcg.com`, `riftcodex.com` et
  `piltoverarchive.com` sont **injoignables** (échec TLS, y compris depuis un
  conteneur) : WARP casse le handshake. Les dumps `raw.githubusercontent.com`,
  eux, passent — raison de plus de préférer l'import par fichiers.

### Le schéma n'a pas de place pour les attributs propres à un jeu

Constaté en confrontant le lot 1 aux données Riftbound réelles : `CARD` porte
`external_ids` mais **aucun champ pour les caractéristiques de jeu**. Le domaine,
le coût d'énergie, la puissance et le *might* de Riftbound n'ont nulle part où
aller — pas plus que le type, les PV ou le stade d'un Pokémon.

Les normaliser en colonnes est exclu : le vocabulaire diffère d'un jeu à
l'autre, c'est exactement ce que `RARITY` et `FINISH` évitent déjà en étant
rattachées à `TCG`. Une colonne `attributes` en JSONB sur `CARD` réglerait le
cas. **À trancher avant le lot 2**, parce que l'import Pokémon perdrait sinon
ces données en silence et qu'il faudrait tout réimporter.

### Poste de développement

- Les ports **8000, 8090 et 5173 sont occupés** sur le poste Windows de
  référence. Renseigner `API_HOST_PORT` (et `POSTGRES_HOST_PORT`) dans `.env`
  plutôt que de garder les valeurs par défaut — sinon `docker compose up`
  échoue sur le bind.
- Sous Git Bash, préfixer par `MSYS_NO_PATHCONV=1` tout `docker run -v … -w
  /chemin` : sinon `/app` est converti en chemin Windows et le démon refuse
  (« working directory 'C:/Program Files/Git/app' is invalid »).

# CLAUDE.md — conventions et état du projet

Fichier de travail pour Claude Code. **À tenir à jour à chaque fin de lot.**
La spec de référence est dans `docs/SPEC.md`.

---

## État d'avancement

| Lot | Sujet | État |
|---|---|---|
| 0 | Fondations (compose, FastAPI, Alembic) | ✅ terminé |
| 1 | Schéma de données multi-TCG | ✅ terminé |
| 1 bis | Schéma : localisation, attributs de jeu, types de carte | ✅ terminé |
| 2 | Import référentiel Pokémon (TCGdex) | ✅ terminé |
| 2 bis | Import référentiel Riftbound | ⏳ à faire |
| 3 | API de recherche | ✅ terminé |
| 4 | Gestion de la collection | ✅ terminé |
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

# Import du référentiel (lot 2) — idempotent, relançable
docker compose exec api python -m app.importers.cli --languages en,fr
docker compose exec api python -m app.importers.cli --expansions swsh3   # essai
docker compose exec api python -m app.importers.cli --force              # tout refaire

# Collection (lot 4)
curl 'localhost:8000/api/v1/collection/items?q=pikachu'
curl 'localhost:8000/api/v1/collection/stats'
curl 'localhost:8000/api/v1/collection/completion?tcg=pokemon'

# API de recherche (lot 3)
curl 'localhost:8000/api/v1/cards?q=dracaufeu'
curl 'localhost:8000/api/v1/cards/809'
open http://localhost:8000/docs          # doc interactive

# Tests — 43, dont ceux de l'API qui ont besoin de la base.
# Les tests sont montés dans le conteneur, pas embarqués dans l'image.
docker compose exec api pip install pytest pytest-asyncio   # une fois
docker compose exec api python -m pytest tests/ -q

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

Vérifier les conditions d'utilisation et signaler toute restriction d'usage ou
de redistribution.

- **TCGdex** : vérifié le 15/09/2026. Licence **MIT**, code *et* base de
  données. Redistribution autorisée, aucune limite de débit publiée. Le dépôt
  précise n'être ni produit ni affilié à Nintendo / The Pokémon Company. Rien
  n'entrave notre usage. Détail dans `docs/IMPORT.md`.
- ⚠️ La licence MIT couvre la **base**, pas les **images** : elles relèvent du
  droit des éditeurs. On ne stocke que des URLs, jamais les fichiers.
- **pokemontcg.io / Cardmarket** : à traiter au lot 5 (voir points ouverts).

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

### TCGdex est auto-hébergé, pas consommé en ligne

Le `docker-compose.yml` embarque `tcgdex/server:edge` (MIT, données incluses).
Pas de limite de débit, pas de dépendance réseau à l'import, et l'inspection
TLS de certains postes ne casse plus rien. L'import complet tombe à 8 minutes.
`CI=true` est **obligatoire** sur ce conteneur — sans lui le port 3000 ne
s'ouvre jamais. Voir `docs/IMPORT.md`.

### La première langue importée fait foi pour l'invariant

TCGdex renvoie la rareté **traduite**. Importer le français en premier
donnerait un code de rareté `peu_commune` et figerait l'interface. D'où
`--languages en,fr` : l'anglais porte les codes, les autres langues n'ajoutent
que des localisations et des libellés, qui **fusionnent** dans `labels`.

102 cartes n'existent qu'en français : elles prennent le français pour
référence, faute de mieux.

### Finitions : union des deux descriptions de TCGdex

`variants` (booléens, seule source de `firstEdition`) et `variants_detailed`
(seule source de `lenticular`, `metal`, `jumbo` et des identifiants de prix)
**divergent sur 13 % des cartes**. On prend l'union.

Trois constats mesurés, à ne pas réapprendre :

- `variantId` n'est **pas** un identifiant d'impression : `endfynwn4n10gzq` est
  porté par 8 914 cartes, `generated` par 8 861. Jamais utilisé comme clé.
- Les libellés de finition sont traduits (`Métal` / `metal`) : le code est
  normalisé et résolu contre une liste fermée. Un type inconnu devient un
  avertissement, jamais un code inventé.
- 1 762 cartes portent deux entrées de même finition avec des identifiants
  Cardmarket différents. Une seule impression est créée, et `external_ids`
  conserve **tous** les identifiants en liste.

### Recherche : le terme est normalisé par PostgreSQL, pas par Python

`card_localization.name_normalized` est une colonne générée. Normaliser la
requête côté Python avec un `unicodedata` maison finirait par diverger du
dictionnaire `unaccent` de la base, et la recherche raterait des cartes **en
silence**. On paie donc un aller-retour SQL pour normaliser avec *la même
fonction*. Ne pas « optimiser » ça.

L'opérateur `%` (trigram) et `LIKE '%…%'` sont complémentaires et utilisent le
**même** index GIN : le premier absorbe les fautes, le second attrape les
sous-chaînes que le trigram note trop bas. Les jokers saisis par l'utilisateur
sont échappés — l'antislash en premier, sinon il ré-échappe le reste.

### Les filtres portent sur les `code`, jamais sur les libellés

`rarity=uncommon` fonctionne, `rarity=Peu Commune` renvoie zéro. Un libellé
change avec la langue, un code non. Même règle pour toutes les sorties : ce
qui est traduit sort en dictionnaire par langue, jamais résolu par le serveur.

### Collection : on ne regroupe jamais par nom

« Pikachu » désigne des dizaines de cartes. La clé de regroupement est
l'**impression** — carte précise, extension précise, finition et langue
précises. Dix Pikachu dont cinq identiques donnent quatre entrées, dont une à
5. Le nom n'est jamais un identifiant, nulle part.

### Ajouter : incrémenter sans prix, créer un lot avec prix

Un ajout sans prix fusionne avec la ligne existante de même impression et même
état (`200`) : c'est le geste courant. Un ajout **avec** prix crée un lot
distinct (`201`), parce qu'un prix de revient lui est propre et que le
fusionner le perdrait. `unit_purchase_price` est un prix **unitaire** — la
colonne a été renommée en migration `0006` pour que le nom porte l'info.

### La vente est une table séparée, et l'historique survit à la collection

Deux raisons : une vente partielle ne peut pas se représenter par une ligne
« à moitié vendue », et vendre son dernier exemplaire supprime la ligne de
collection alors que la vente doit rester. `collection_item_id` est nullable,
en `SET NULL`, et n'est renseigné que si un seul lot a été touché **et qu'il
survit** — pointer un lot vidé dans la même transaction violerait la FK.

### Aucune conversion de devise, jamais

Tous les totaux sont rendus **par devise**. Un taux inventé produirait un
chiffre faux et crédible. Les exemplaires sans prix saisi sont comptés à part
(`items_without_price`) : ils ne valent pas zéro, leur prix est inconnu.

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

### Lot 2 — TCGdex ✅ fait

Référentiel en base : 23 649 cartes, 64 301 impressions, 221 extensions.
Tout est documenté dans `docs/IMPORT.md`. Reste ouvert :

- Une carte de l'extension `exu` a pour identifiant littéral `?` et renvoie 404
  dans les deux langues. Anomalie **en amont**, signalée au rapport d'import.
- `w_promo` existe dans le vocabulaire TCGdex mais n'est jamais vrai : aucune
  finition créée. Revérifier après une mise à jour de l'image.
- Les `labels` de `first_edition` sont vides — la source n'en fournit pas.
- L'idempotence s'appuie sur les clés naturelles, pas sur `external_ids` comme
  anticipé : `(extension, numéro)` pour une carte, `(carte, finition, langue)`
  pour une impression. Plus simple et plus sûr.

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

### Lot 3 — API de recherche ✅ fait

Critère de fin vérifié : les 24 correspondances exactes de « dracaufeu » sont
toutes dans le résultat de « charizard ». Détail dans `docs/API.md`. Reste
ouvert, à traiter avec le front du lot 6 sur des cas réels :

- Une faute peut classer un voisin plus court devant la bonne carte :
  `dracofeu` remonte « Draco » (0,50) avant « Dracaufeu » (0,46). Verdict du
  trigram, pas un bug — `word_similarity` donne le même ordre.
- Deux fautes dans un nom court passent sous `pg_trgm.similarity_threshold`
  (0,3) : `sharizrd` ne trouve pas Charizard. Abaisser le seuil ramènerait du
  bruit — à régler sur des recherches réelles, pas à l'aveugle.
- Sous 3 caractères l'index trigram ne sert plus (parcours séquentiel, ~10 ms
  sur 45 000 lignes). Laissé passer plutôt qu'interdit.

### Lot 4 — Collection ✅ fait

Documenté dans `docs/COLLECTION.md`. Décisions arbitrées avec l'utilisateur le
15/09/2026 :

- Les **ventes** ne figuraient pas à la spec : ajoutées sur demande, avec la
  plateforme de vente. Tables `collection_sale` et `sale_platform`.
- La **plus-value** a été écartée : elle se lit sur les deux totaux.
- La **complétion** compte une carte quelle que soit sa langue (FR, EN, JP…),
  avec `language` en filtre optionnel. Deux dénominateurs rendus : `official`
  (total imprimé) et `total` (cartes secrètes comprises).
- Seuls **FR et EN** sont en base. Pour CN/ES/JP : relancer l'import du lot 2
  avec `--languages en,fr,ja,es`.

Reste ouvert :

- Frais de port et commissions de vente non modélisés : les ajouter à moitié
  fausserait le total des ventes.
- Cartes gradées (PSA, BGS) : axe distinct de `condition`, pas au schéma.
- Annuler une vente ne remet rien en collection — on ne sait pas dans quel lot.

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

### Lot 2 bis — Riftbound (reporté)

**Décision du 15/09/2026 : on se concentre sur Pokémon d'abord.** Riftbound
attend qu'une chaîne complète tourne sur un seul jeu — recherche, collection,
prix — avant d'ajouter un second TCG. Le schéma est prêt à l'accueillir : une
ligne dans `tcg`, ses `finish` et `rarity`, un import. Aucune migration.

L'exploration ci-dessous reste valable pour le jour où on s'y remettra.

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

### Ce que la langue change — et ce qu'elle ne change pas

Réglé par la migration `0004`. La règle, à connaître avant d'écrire le moindre
import :

| Ce qui est **invariant** | Ce qui est **localisé** |
|---|---|
| `hp`, `retreat`, `dexId`, `regulationMark` | nom, rareté, stade, types, attaques |
| illustrateur, numéro, finition | **l'image et son pHash** |
| `domain`, `energyCost`, `might` (Riftbound) | texte de règles, nom d'extension |

L'image est le piège : une carte française et son équivalent anglais partagent
l'illustration mais pas l'image imprimée. D'où `image_url` et `image_phash` dans
`card_localization` et non dans `card` — sinon le scan photo du lot 7 comparerait
une photo de carte française à des empreintes anglaises.

### Attributs de jeu : JSONB pour l'affichage, table pour le filtre

`card.attributes`, `card_localization.attributes` et `printing.attributes`
accueillent ce qui est propre à chaque jeu. Les deux jeux n'ont **aucun attribut
en commun** (vérifié, pas supposé), donc des colonnes typées auraient donné une
table à moitié vide de chaque côté.

La limite est nette : ce qui sert à **filtrer** reste une table de référence
rattachée au TCG — `rarity`, `finish`, et désormais `card_type`. Ce qui sert à
**afficher** va dans le JSONB. Ne pas déplacer un filtre du lot 3 vers
`attributes` sous prétexte que c'est plus rapide à écrire.

### Vocabulaires : un `code` invariant, des `labels` par langue

`rarity`, `finish` et `card_type` portent `labels` en JSONB
(`{"fr": "Peu Commune", "en": "Uncommon"}`) et non un libellé unique : TCGdex
renvoie la rareté traduite, et un import français aurait figé l'interface en
français. Le `code` reste la clé d'upsert, et il ne doit jamais dépendre de la
langue importée.

### Poste de développement

- Les ports **8000, 8090 et 5173 sont occupés** sur le poste Windows de
  référence. Renseigner `API_HOST_PORT` (et `POSTGRES_HOST_PORT`) dans `.env`
  plutôt que de garder les valeurs par défaut — sinon `docker compose up`
  échoue sur le bind.
- Sous Git Bash, préfixer par `MSYS_NO_PATHCONV=1` tout `docker run -v … -w
  /chemin` : sinon `/app` est converti en chemin Windows et le démon refuse
  (« working directory 'C:/Program Files/Git/app' is invalid »).

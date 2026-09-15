# CLAUDE.md — conventions et état du projet

Fichier de travail pour Claude Code. **À tenir à jour à chaque fin de lot.**
La spec de référence est dans `docs/SPEC.md`.

---

## État d'avancement

| Lot | Sujet | État |
|---|---|---|
| 0 | Fondations (compose, FastAPI, Alembic) | ✅ terminé |
| 1 | Schéma de données multi-TCG | ⏳ à faire |
| 2 | Import référentiel Pokémon (TCGdex) | ⏳ à faire |
| 3 | API de recherche | ⏳ à faire |
| 4 | Gestion de la collection | ⏳ à faire |
| 5 | Prix et historique | ⏳ à faire |
| 6 | Front PWA | ⏳ à faire |
| 7 | Reconnaissance par photo | ⏳ à faire |

**Règle de travail : un lot à la fois. On s'arrête en fin de lot et on attend
une validation explicite avant de démarrer le suivant.**

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
docker compose exec api ruff check app
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

### Dockerfile : stub `app/` avant le `pip install`

Les dépendances s'installent avant la copie du code pour garder le layer en
cache. Comme setuptools a besoin que le paquet existe, on pose un `app/`
vide puis on le supprime ; `PYTHONPATH=/srv/app` garantit que c'est bien le
code réellement copié qui est importé, pas le résidu en `site-packages`.

---

## Points ouverts

- **Lot 1** : le mapping des finitions (normale / reverse / holo / 1st ed.) doit
  rester ouvert — on ne connaîtra la réalité des données qu'au lot 2, après
  exploration de l'API TCGdex.
- **Lot 2** : explorer et **montrer la structure réelle** renvoyée par TCGdex
  avant d'écrire le moindre mapping.
- **Lot 5** : vérifier l'état actuel de l'API pokemontcg.io avant de coder ;
  le connecteur de prix passe derrière une interface abstraite.
- **Riftbound** : hors périmètre jusqu'après le lot 5, mais le schéma du lot 1
  doit pouvoir l'accueillir sans migration douloureuse.

# Mycards — gestionnaire de collection TCG

Application de gestion de collection de cartes TCG (web + mobile via PWA),
conçue **multi-jeux dès le départ** : Pokémon d'abord, Riftbound ensuite.

La spécification complète et le découpage en lots sont dans [`docs/SPEC.md`](docs/SPEC.md).

## Stack

| Couche | Choix |
|---|---|
| API | Python 3.12, FastAPI, SQLAlchemy 2.x (async) |
| Migrations | Alembic |
| Base | PostgreSQL 16 (`pg_trgm`, `unaccent`) |
| Conteneurisation | Docker Compose |
| Front | PWA — framework choisi au lot 6 |

## Démarrage

```bash
cp .env.example .env        # puis éditez POSTGRES_PASSWORD et DATABASE_URL
docker compose up --build
```

Puis :

- API : http://localhost:8000
- Santé : http://localhost:8000/health
- Doc OpenAPI : http://localhost:8000/docs

`docker compose up` attend que Postgres soit *healthy*, applique les migrations
Alembic, puis démarre l'API.

Les URLs ci-dessus supposent `API_HOST_PORT=8000`. Si ce port est déjà pris sur
votre machine, changez-le dans `.env` — le conteneur, lui, écoute toujours 8000.

### Sur un poste Windows

Le dépôt force les fins de ligne en LF via `.gitattributes`. Sans cela,
`docker-entrypoint.sh` part en CRLF dans l'image et le conteneur `api` meurt sur
son shebang (`env: 'bash\r': No such file or directory`) en redémarrant à
l'infini. Si vous avez cloné le dépôt **avant** l'ajout de ce fichier, forcez un
re-checkout, l'attribut ne s'applique pas rétroactivement :

```bash
rm backend/docker-entrypoint.sh && git checkout -- backend/docker-entrypoint.sh
file backend/docker-entrypoint.sh   # ne doit plus indiquer « CRLF line terminators »
```

## Vérifier que tout tourne

```bash
curl -s http://localhost:8000/health
# {"status":"ok","api":"ok","database":"ok"}

docker compose exec db psql -U tcg -d tcg -c "SELECT extname FROM pg_extension;"
```

`/health` renvoie **503** si PostgreSQL est injoignable : c'est une sonde utile,
pas un simple « je suis en vie ».

## Arborescence

```
.
├── docker-compose.yml       # Postgres + API
├── .env.example             # modèle de configuration (le .env est gitignoré)
├── docs/SPEC.md             # spécification et découpage en lots
├── docs/SCHEMA.md           # schéma de données commenté
├── CLAUDE.md                # conventions, commandes, état d'avancement
└── backend/
    ├── Dockerfile
    ├── docker-entrypoint.sh # attente DB + alembic upgrade head
    ├── pyproject.toml
    ├── alembic.ini
    ├── alembic/             # migrations
    └── app/
        ├── main.py
        ├── core/config.py   # settings pydantic
        ├── db/              # base déclarative + sessions
        ├── models/          # modèles ORM (lot 1)
        └── api/routes/      # endpoints
```

## Avancement

- [x] **Lot 0** — Fondations
- [x] **Lot 1** — Schéma de données ([`docs/SCHEMA.md`](docs/SCHEMA.md))
- [ ] Lot 2 — Import du référentiel Pokémon (TCGdex)
- [ ] Lot 3 — API de recherche
- [ ] Lot 4 — Gestion de la collection
- [ ] Lot 5 — Prix et historique
- [ ] Lot 6 — Front PWA
- [ ] Lot 7 — Reconnaissance de carte par photo

## Licence et données

Ce dépôt ne contient **ni images de cartes, ni dump du référentiel** (droits
d'auteur des éditeurs). Seules des URLs sont stockées, et le référentiel se
reconstruit via le script d'import du lot 2.

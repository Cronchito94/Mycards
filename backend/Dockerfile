# syntax=docker/dockerfile:1

FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONPATH=/srv/app

WORKDIR /srv/app

# Les dépendances sont installées avant le code applicatif : tant que
# pyproject.toml ne bouge pas, ce layer reste en cache.
# Le paquet `app` n'existe pas encore à ce stade, on pose un stub pour que
# setuptools puisse résoudre le projet ; PYTHONPATH=/srv/app garantit ensuite
# que c'est bien le code copié plus bas qui est importé, pas le stub.
COPY pyproject.toml ./
RUN mkdir -p app && touch app/__init__.py \
    && pip install . \
    && rm -rf app

COPY alembic.ini ./
COPY alembic ./alembic
COPY app ./app
COPY docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
RUN chmod +x /usr/local/bin/docker-entrypoint.sh

# psycopg[binary] embarque libpq : aucun paquet système supplémentaire requis.

EXPOSE 8000

ENTRYPOINT ["docker-entrypoint.sh"]
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]

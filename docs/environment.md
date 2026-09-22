# Environment files and variables

Two `.env` files, read by two different programs. Both are git-ignored; both have a committed
`.env.example` next to them.

| File | Read by | Purpose | Template |
|---|---|---|---|
| **`.env`** (repo root) | **Docker Compose itself**, before any container starts | Values interpolated into `compose.yaml` — ports, nothing application-level | `.env.example` |
| **`backend/.env`** | **Django** (via `python-dotenv` in `settings.py`) *and* the `db`, `web`, `worker` containers (via `env_file:`) | Everything the application needs: secret key, database, encryption key, providers | `backend/.env.example` |

Compose does **not** pass the root `.env` into containers, and `env_file:` values are **not** available
to compose's own `${...}` interpolation. That is why the nginx port lives in the root file and everything
else in `backend/.env`.

## Setup from a fresh clone

```bash
cp .env.example .env
cp backend/.env.example backend/.env
python -c "import secrets; print('SECRET_KEY=' + secrets.token_urlsafe(50))"
python -c "from cryptography.fernet import Fernet; print('FIELD_ENCRYPTION_KEY=' + Fernet.generate_key().decode())"
# paste both into backend/.env, set POSTGRES_PASSWORD, then:
docker compose up -d --build
```

Then `http://localhost:${HTTP_PORT}/api/v1/docs`.

> **Never commit either `.env`.** `SECRET_KEY`, `FIELD_ENCRYPTION_KEY` and `POSTGRES_PASSWORD` are real
> secrets — and losing `FIELD_ENCRYPTION_KEY` makes every encrypted ID/passport and bank account number
> permanently unreadable. Back it up somewhere safe before going anywhere near production.

---

## 1. Root `.env` — Docker Compose only

| Variable | Default | What it does |
|---|---|---|
| `HTTP_PORT` | `80` | Host port nginx publishes (`"${HTTP_PORT:-80}:80"`). Windows commonly reserves 80 for IIS/http.sys, so the example uses **8080**. |

Compose also understands `COMPOSE_PROJECT_NAME`, `COMPOSE_FILE`, `DOCKER_HOST` etc. if you ever need them;
this project sets `name: asknget` in `compose.yaml` instead.

## 2. `backend/.env` — Django and the containers

### Required

| Variable | Example | Notes |
|---|---|---|
| `SECRET_KEY` | *(50 random chars)* | Django signing key; also signs JWTs. Changing it logs everyone out. |
| `FIELD_ENCRYPTION_KEY` | *(Fernet key)* | Encrypts ID/passport and bank account numbers at rest (`core.fields.EncryptedTextField`). **Lose it and that data is unrecoverable.** Tests generate a throwaway key. |
| `POSTGRES_USER` / `POSTGRES_PASSWORD` / `POSTGRES_DB` | `asknget` / *(secret)* / `asknget` | Consumed by the **`db` image** on first start to create the role and database. Changing them later does nothing unless you also wipe the volume (`docker compose down -v`). |
| `DATABASE_URL` | `postgres://asknget:…@db:5432/asknget` | Host is **`db`** inside Docker, **`localhost`** from your PC (the dev override publishes 5432). Unset → SQLite at `backend/asknget/db.sqlite3`. |

### Django basics

| Variable | Default | Notes |
|---|---|---|
| `DEBUG` | `1` | `0` in production. Compose's dev override forces `1`. |
| `ALLOWED_HOSTS` | `localhost,127.0.0.1` | Comma-separated. **Include the country subdomains** — `asknget.com,.asknget.com` — because `za.asknget.com` is how the API resolves the market. Locally: `localhost,.localhost,127.0.0.1`. |
| `CSRF_TRUSTED_ORIGINS` | *(empty)* | Comma-separated **with scheme**, e.g. `https://api.asknget.com`. Needed once a browser posts to the admin behind a proxy. |
| `BEHIND_PROXY` | *(unset)* | `1` makes Django trust `X-Forwarded-Proto`/`Host` from nginx. Set by `compose.yaml` for the `web` service. |
| `MEDIA_ROOT` | `backend/asknget/media` | Where uploads land. Compose sets `/app/asknget/media`. |
| `SERVE_MEDIA` | `1` when `DEBUG` else `0` | Whether Django serves `/media/`. In the full stack **nginx** does, so compose sets `0`. |
| `STOREFRONT_URL` | *(empty)* | Public buyer site, used to build "View Product" links in the seller portal. |

### Markets (sprint 4)

| Variable | Default | Notes |
|---|---|---|
| `DEFAULT_COUNTRY` | `ZA` | Fallback market when a request carries no region (bare domain, tests, management commands). The countries themselves — currency, timezone, languages, KYC and return policy — are **rows in the database**, edited in Django admin, not env vars. |

### Search & recommendations (sprint 3)

| Variable | Default | Notes |
|---|---|---|
| `EMBEDDING_PROVIDER` | `hash` | `hash` = offline lexical embeddings, no key, fine for dev/tests. `voyage` or `openai` for real semantics. |
| `EMBEDDING_MODEL` | *(provider default)* | `voyage-3.5-lite` / `text-embedding-3-small`. |
| `EMBEDDING_DIMENSIONS` | `512` | Baked into the pgvector column — changing it needs a migration **and** `manage.py embed_products --all`. |
| `VOYAGE_API_KEY` / `OPENAI_API_KEY` | *(empty)* | Only the chosen provider's key is needed. **Only product text is ever sent to these services** — never customer data. |
| `SEARCH_MAX_CANDIDATES` | `200` | How deep a query can rank. |
| `ANALYTICS_EVENT_SCAN_LIMIT` | `5000` | Rows the premium search-terms report scans before it reports `truncated`. |

### Auth / OTP

| Variable | Default |
|---|---|
| `JWT_ACCESS_TTL_MINUTES` | `15` |
| `JWT_REFRESH_TTL_DAYS` | `30` |
| `OTP_TTL_MINUTES` | `10` |
| `OTP_RESEND_COOLDOWN_SECONDS` | `45` (the design's "Resend code in 00:45") |
| `OTP_MAX_ATTEMPTS` | `5` |

### Performance and serving

| Variable | Default | Notes |
|---|---|---|
| `WEB_CONCURRENCY` | `3` (Dockerfile) | gunicorn worker processes; ≈ 2 × CPU + 1 on a dedicated box, ~100 MB RAM each. |
| `GUNICORN_CMD_ARGS` | `--threads 2` (Dockerfile) | Any other gunicorn flag. |
| `DB_POOL` | `1` | psycopg connection pool (Postgres only). `0` disables it; pooling and `CONN_MAX_AGE` are mutually exclusive. |
| `ZEAL` | *(unset)* | `1` arms the N+1 detector in a dev server. Always on under `manage.py test`. |

> Note: values under `environment:` in `compose.yaml` **override** `env_file:`. That is why
> `SERVE_MEDIA`, `BEHIND_PROXY` and `MEDIA_ROOT` are fixed there, and why gunicorn's sizing defaults live
> in the `Dockerfile` (so `backend/.env` can still override them).

### Container-only

| Variable | Where | Notes |
|---|---|---|
| `SKIP_MIGRATE` | `docker-entrypoint.sh` | `1` skips wait-for-db + migrate + collectstatic. Set for the `worker` so it does not race `web`. |

---

## 3. Which file does what, per workflow

| You run | Reads |
|---|---|
| `docker compose up -d --build` | root `.env` (HTTP_PORT) **+** `backend/.env` (into db/web/worker) |
| `uv run poe dev` / `poe test` (local) | `backend/.env`, with `DATABASE_URL` overridden to SQLite by the task |
| `uv run python manage.py …` from `backend/asknget/` | `backend/.env` — so `DATABASE_URL` points at `db`, which does not resolve on your PC. Either use the poe tasks or prefix `DATABASE_URL=sqlite:///db.sqlite3`. |
| `manage.py test` (any way) | `backend/.env`, but a throwaway Fernet key and a fast password hasher are forced in `settings.py` |

Environment variables already set in your shell **win** over `backend/.env`: `python-dotenv` is loaded
with its default `override=False`. Handy for one-offs (`DATABASE_URL=… uv run …`), surprising if you
exported something months ago and forgot.

## 4. Production checklist

- `DEBUG=0`, a fresh `SECRET_KEY`, real `ALLOWED_HOSTS` (plus every country subdomain) and
  `CSRF_TRUSTED_ORIGINS`.
- `FIELD_ENCRYPTION_KEY` backed up in a secrets manager, not only in the file.
- `POSTGRES_PASSWORD` not the example value; consider `docker compose -f compose.yaml up` (no dev
  override → gunicorn, no bind mounts, no published Postgres).
- `BEHIND_PROXY=1` and TLS terminated at nginx.
- Uploads (`media`) and `pgdata` are named volumes — include them in backups, or move media to object
  storage.
- Secrets in a real deployment belong in the orchestrator's secret store (Docker/Swarm secrets, k8s
  Secrets, SSM…), with `env_file` kept only for local work.

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
# paste it into backend/.env and set POSTGRES_PASSWORD. Leave FIELD_ENCRYPTION_KEY exactly as the
# example has it: it is a shared development key, so every developer reads the same encrypted rows.
docker compose up -d --build
```

Then `http://localhost:${HTTP_PORT}/api/v1/docs`.

> **Never commit either `.env`.** In production `SECRET_KEY`, `FIELD_ENCRYPTION_KEY` and `POSTGRES_PASSWORD` are real
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
| `FIELD_ENCRYPTION_KEY` | *(Fernet key)* | Encrypts ID/passport and bank account numbers at rest (`core.fields.EncryptedTextField`). **Lose it and that data is unrecoverable.** `.env.example` ships a **shared development key** so every developer's database is interchangeable; production must generate its own (`manage.py check` warns, `core.W003`, if it sees the shared one with `DEBUG=0`). Tests generate a throwaway key. |
| `FIELD_ENCRYPTION_KEY_PREVIOUS` | *(comma-separated Fernet keys)* | Optional. Keys this database used before. Rows written under them stay readable; everything new uses `FIELD_ENCRYPTION_KEY`. See *Key mismatch* under Troubleshooting. |
| `POSTGRES_USER` / `POSTGRES_PASSWORD` / `POSTGRES_DB` | `asknget` / *(secret)* / `asknget` | Consumed by the **`db` image** on first start to create the role and database. Changing them later does nothing unless you also wipe the volume (`docker compose down -v`). |
| `DATABASE_URL` | `postgres://asknget:…@db:5432/asknget` | Host is **`db`** inside Docker, **`localhost`** from your PC (the dev override publishes 5432). Unset → SQLite at `backend/asknget/db.sqlite3`. |

### Django basics

| Variable | Default | Notes |
|---|---|---|
| `DEBUG` | `1` | `0` in production. Compose's dev override forces `1`. |
| `ALLOWED_HOSTS` | `localhost,127.0.0.1` | Comma-separated. **Include the country subdomains** — `asknget.com,.asknget.com` — because `za.asknget.com` is how the API resolves the market. Locally: `localhost,.localhost,127.0.0.1`. |
| `CSRF_TRUSTED_ORIGINS` | *(empty)* | Comma-separated **with scheme**, e.g. `https://api.asknget.com`. Needed once a browser posts to the admin behind a proxy. |
| `CORS_ALLOWED_ORIGINS` | *(empty)* | See **CORS** below. |
| `BEHIND_PROXY` | *(unset)* | `1` makes Django trust `X-Forwarded-Proto`/`Host` from nginx. Set by `compose.yaml` for the `web` service. |
| `MEDIA_ROOT` | `backend/asknget/media` | Where uploads land. Compose sets `/app/asknget/media`. |
| `SERVE_MEDIA` | `1` when `DEBUG` else `0` | Whether Django serves `/media/`. In the full stack **nginx** does, so compose sets `0`. |
| `STOREFRONT_URL` | *(empty)* | Public buyer site, used to build "View Product" links in the seller portal. |

### CORS

Only relevant when the storefront runs on a **different origin** than the API. If each market's frontend
is served from the same host as its API (`za.asknget.com` serving both the app and `/api/`), leave every
value empty — the middleware then adds no headers at all and nothing changes.

| Variable | Default | Notes |
|---|---|---|
| `CORS_ALLOWED_ORIGINS` | *(empty)* | Comma-separated, exact origins: scheme + host + port, no trailing slash — `https://asknget.com,http://localhost:3000`. |
| `CORS_ALLOWED_ORIGIN_REGEXES` | *(empty)* | Comma-separated regexes, for country subdomains: `^https://[a-z]{2}\.asknget\.com$`. Anchor them (`^…$`) or `https://asknget.com.evil.io` matches. |
| `CORS_ALLOW_ALL_ORIGINS` | `0` | Staging convenience. Safe-ish only because credentials are off; never pair the two. |
| `CORS_ALLOW_CREDENTIALS` | `0` | The API authenticates with an `Authorization: Bearer` header, not cookies, so a browser has no ambient credentials to send — a hostile page can only ever make *anonymous* calls. Turn on only if cookie auth is added. |

Fixed in code, not env, because they track the API's own headers:

- **Allowed request headers** include our custom ones — `X-Cart-Token` (guest carts) and
  `X-Region` / `X-Language` / `X-Timezone` (markets). Without these, the browser's preflight fails and
  the real request is never sent.
- **Exposed response headers** are `X-Region`, `Content-Language` and `Content-Disposition` — a
  cross-origin page cannot read *any* custom response header unless it is listed here, so region
  detection and the CSV export filename would silently break.
- `CORS_URLS_REGEX = ^/api/.*$` — `/admin` and `/static` never get CORS headers.

While `DEBUG=1` and nothing is configured, any `http://localhost:<port>` / `http://127.0.0.1:<port>`
origin is allowed, so a local Next/Vite frontend works on a fresh clone. Production sets `DEBUG=0`, where
that default does not apply.

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

## 4. Troubleshooting

**`ImproperlyConfigured: FIELD_ENCRYPTION_KEY is not set`** — usually on
`PATCH /api/v1/seller/application/steps/basic-info`, the first request that writes an encrypted column
(`id_passport_number`). `backend/.env` is missing or the key line is empty. Fill it and restart; a
*new* key makes any rows written with the old one unreadable (`unable to decrypt value (key
mismatch?)`), so restore the original key if one exists rather than generating another.
`manage.py check` (and so `runserver` and `uv run poe check`) warns about a missing or malformed key
at startup — `core.W001` / `core.W002` — so this should not reach a request again.

**`env file ...\backend\.env not found`** from `docker compose` — same missing file; compose loads it
via `env_file` and refuses to start without it. Note the db container only applies
`POSTGRES_PASSWORD` when it *creates* `pgdata`: after recreating `.env` with a new password, either
put the old password back or `docker compose down -v` (which erases the database).

**`no such table` / empty data locally** — the poe tasks point at `backend/asknget/db.sqlite3`; a bare
`manage.py` run uses `DATABASE_URL` from `.env`, which is the Docker host `db`. See the table above.

**`Cannot decrypt sellers.SellerApplication.id_passport_number ...`** — a row was written under a
different `FIELD_ENCRYPTION_KEY` than the one you are running. It happens when two developers each
generate their own key and then share a database, a dump or a committed `db.sqlite3`.

1. See how bad it is. The command reads raw rows, so it works even when the ORM would raise:
   `uv run poe manage check_encryption` (Docker: `poe dmanage check_encryption`). It prints a fingerprint
   of each key (the first 8 hex digits of its sha256, which cannot be reversed into the key) and, per field,
   how many rows are readable. Fingerprints are the way to compare two people's keys.
2. If the other key still exists, keep yours as `FIELD_ENCRYPTION_KEY` and add theirs to
   `FIELD_ENCRYPTION_KEY_PREVIOUS`. Everything is readable again at once. Get it from them through your
   password manager or another secure channel, not chat or e-mail.
3. `check_encryption --rewrite` then re-encrypts those rows under your key, after which the old key can
   be removed from the file.
4. If nobody has the key any more, those rows cannot be recovered. `check_encryption` exits 1 and leaves
   them untouched. For a development database, delete it and run `uv run poe seed` again.

To avoid it altogether: use the shared development key from `backend/.env.example` for every
non-production database, and never move a database between machines that used different keys.

## 5. Production checklist

- `DEBUG=0`, a fresh `SECRET_KEY`, real `ALLOWED_HOSTS` (plus every country subdomain) and
  `CSRF_TRUSTED_ORIGINS`.
- A production-only `FIELD_ENCRYPTION_KEY` (never the shared development one), backed up in a secrets
  manager, not only in the file. To rotate: move the old value to `FIELD_ENCRYPTION_KEY_PREVIOUS`, set the
  new one, run `manage.py check_encryption --rewrite`, then drop the old key.
- `POSTGRES_PASSWORD` not the example value; consider `docker compose -f compose.yaml up` (no dev
  override → gunicorn, no bind mounts, no published Postgres).
- `BEHIND_PROXY=1` and TLS terminated at nginx.
- Uploads (`media`) and `pgdata` are named volumes — include them in backups, or move media to object
  storage.
- Secrets in a real deployment belong in the orchestrator's secret store (Docker/Swarm secrets, k8s
  Secrets, SSM…), with `env_file` kept only for local work.

---

## 6. Deploying the API on Vercel

`main` deploys as it is: there is no separate Vercel branch to maintain. Vercel's Python runtime is 3.12,
so the project supports `>=3.12` (Docker still builds on 3.13), and `uv.lock` is kept.

**Set these in the Vercel project (Settings → Environment Variables), for Production and Preview:**

| Variable | Value |
|---|---|
| `DATABASE_URL` | A hosted Postgres (Neon, Supabase…). **Required**: the app refuses to start on Vercel without it, because SQLite cannot work there (read-only filesystem, `/tmp` wiped between invocations). Prefer the provider's *pooled* connection string. |
| `SECRET_KEY` | A fresh random value. Also signs the login tokens: left unset, the public development default is used. |
| `DEBUG` | `0`. The default is `1`. |
| `FIELD_ENCRYPTION_KEY` | A key generated for this deployment, not the shared development one (`manage.py check` warns, `core.W003`, if it sees that). Back it up: it cannot be recovered. |
| `CORS_ALLOWED_ORIGINS` | The frontend, exactly: `https://asknget-frontend-one.vercel.app`. Comma-separated for more than one. |
| `CORS_ALLOWED_ORIGIN_REGEXES` | Optional, for the frontend's preview deployments, e.g. `^https://asknget-frontend-[a-z0-9-]+\.vercel\.app$`. Keep it specific to your project: `^https://.*\.vercel\.app$` would let anyone's Vercel site call the API. |

**Automatic when Vercel is detected (`VERCEL=1`):** this deployment's own hostnames
(`VERCEL_URL`, `VERCEL_BRANCH_URL`, `VERCEL_PROJECT_PRODUCTION_URL`) are added to `ALLOWED_HOSTS` and
`CSRF_TRUSTED_ORIGINS` as exact hosts (no `*.vercel.app`); the `X-Forwarded-Proto` header is trusted;
connections are not held open and the psycopg pool is off, so a function instance does not exhaust the
database's connection limit; server-side cursors are disabled for transaction poolers.

**Schema and data are not created by Vercel.** Run them once from your machine against the hosted
database, and again after any release that adds migrations:

```bash
cd backend/asknget
DATABASE_URL=<hosted url> uv run python manage.py migrate
DATABASE_URL=<hosted url> uv run python manage.py seed_lookups
```

**Known limits:**
- **Uploads don't persist.** Product images and seller KYC documents are written to the local filesystem,
  which is read-only on Vercel. They need object storage (S3, Vercel Blob, R2) before sellers can upload.
- **Static files** (the admin, the Swagger UI) are not collected by Vercel; whether the API docs render there
  is untested.
- A leading double slash (`https://api//api/v1/...`, from a frontend base URL ending in `/`) is served as
  `/api/v1/...`. `APPEND_SLASH` does nothing for it: `//api/...` is a 404 with the setting on or off.


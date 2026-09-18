# asknget backend

Django 6 + django-ninja API for the asknget marketplace (buyer storefront + seller onboarding).
The data model is derived from the Figma design — see [`../docs/data-model.md`](../docs/data-model.md).

## Layout

```
backend/
├── asknget/              # Django project root (manage.py lives here)
│   ├── asknget/          # settings, urls, api (NinjaAPI root)
│   ├── core/             # abstract bases, EncryptedTextField, Bank + SiteSetting lookups, seed command
│   ├── accounts/         # User (email login), OTP, refresh tokens, terms audit, addresses, payment methods
│   ├── catalog/          # categories, brands, products, variants/options, reviews, wishlist
│   ├── sellers/          # seller KYC application, documents, info requests, stores
│   ├── orders/           # cart, vouchers, delivery methods, orders, status timeline, payments
│   └── marketing/        # banners, newsletter, contact messages
├── .env.example
└── pyproject.toml        # managed with uv
```

## Documentation

- **Developer guide (PDF)** for frontend/stakeholders: [`../docs/asknget-backend-developer-guide.pdf`](../docs/asknget-backend-developer-guide.pdf) — regenerate after API changes:
  `uv run --with reportlab python ../docs/build_developer_guide.py` (from `backend/`).
- Data model: [`../docs/data-model.md`](../docs/data-model.md) · Sprint plan and decisions: [`../docs/sprint-2-plan.md`](../docs/sprint-2-plan.md) · Live API docs: `/api/v1/docs`.

## Setup

```bash
uv sync
cp .env.example .env            # then set FIELD_ENCRYPTION_KEY (see comment in the file)
cd asknget
uv run python manage.py migrate
uv run python manage.py seed_lookups        # categories, brands, banks, delivery methods
uv run python manage.py seed_demo           # dev only: demo accounts, store, products, vouchers (password DemoPass123!)
uv run python manage.py createsuperuser     # optional — seed_demo also creates admin@asknget.demo
uv run python manage.py runserver
```

- API docs (Swagger): http://127.0.0.1:8000/api/v1/docs · OpenAPI JSON: /api/v1/openapi.json
- Admin: http://127.0.0.1:8000/admin/

Without `DATABASE_URL` the project falls back to SQLite; set it to a Postgres URL for anything beyond local experiments.

## Running with Docker

Two images: the API (`backend/Dockerfile`, built on Docker Hub's official `astral/uv` image so dependencies are installed by uv from `uv.lock`, and `uv` ships in the container) and `pgvector/pgvector:pg17` (official Postgres 17 + the pgvector extension used by search/recommendations). The API image runs twice: `web` (HTTP) and `worker` (background jobs — today it keeps product embeddings fresh).
Your code stays where it is — the image *copies* it at build time, and in dev mode the
working tree is *bind-mounted* into the container so edits reload live.

```bash
cp backend/.env.example backend/.env    # fill SECRET_KEY, FIELD_ENCRYPTION_KEY, POSTGRES_PASSWORD (+ same password in DATABASE_URL)
docker compose up -d --build            # dev: Postgres + runserver with live reload on :8000
docker compose exec web python manage.py seed_demo   # optional demo data
```

| I want to… | Command |
|---|---|
| Start / stop (data kept) | `docker compose up -d` · `docker compose down` |
| Start **production-shaped** (gunicorn, no bind mount) | `docker compose -f compose.yaml up -d --build` |
| Rebuild after changing `pyproject.toml` / `uv.lock` / `Dockerfile` | `docker compose up -d --build` |
| Tail logs | `docker compose logs -f web` (or `db`, `worker`) |
| Re-embed the whole catalogue (after switching `EMBEDDING_PROVIDER`) | `docker compose exec web python manage.py embed_products --all` |
| Run any manage.py command | `docker compose exec web python manage.py <cmd>` |
| Use uv inside the container (same as on your PC) | `docker compose exec web uv run python manage.py <cmd>` · `uv lock`, `uv tree` … |
| Run the tests against Postgres | `docker compose exec web python manage.py test` |
| Open a psql shell | `docker compose exec db psql -U asknget -d asknget` |
| Connect a DB tool from Windows (dev only) | host `127.0.0.1`, port `5432`, credentials from `backend/.env` |
| Back up the database | `docker compose exec db pg_dump -U asknget -Fc asknget > backup.dump` |
| Restore | `docker compose exec -T db pg_restore -U asknget -d asknget --clean < backup.dump` |
| Wipe **everything** including data | `docker compose down -v` |
| Publish the image | `docker tag asknget-api:latest <registry>/asknget-api:<tag> && docker push …` |

How the pieces fit:

- `compose.yaml` is the production shape; `compose.override.yaml` is merged automatically for dev
  (bind mount, `runserver`, `DEBUG=1`, Postgres published on localhost). Passing `-f compose.yaml`
  skips the override.
- `backend/.env` is the single config source for both containers **and** for running without Docker.
  `DATABASE_URL` host is `db` inside Docker and `localhost` from your PC.
- On every start the entrypoint waits for Postgres, runs `migrate`, the idempotent `seed_lookups` and
  `embed_products` (missing/stale only), then execs gunicorn (or runserver). Set `SKIP_MIGRATE=1` for
  one-off commands. The `worker` service loops `embed_products --watch` so catalogue edits are searchable
  within ~30 s.
- Data lives in named volumes `asknget_pgdata` (database) and `asknget_media` (uploads); they survive
  `down`, rebuilds and image upgrades. In dev, uploads go to `backend/asknget/media` on disk instead.
- Static files (admin, Swagger) are baked into the image and served by whitenoise; uploads are served
  by Django while `SERVE_MEDIA=1` — move them to object storage or a reverse proxy before real traffic.
- Behind HTTPS/a proxy set `BEHIND_PROXY=1`, `CSRF_TRUSTED_ORIGINS=https://your.domain`, `DEBUG=0`,
  and put your domain in `ALLOWED_HOSTS`.

## Search & recommendations (`discovery` app)

- **Search** — `GET /products?q=` is hybrid: filters apply first, then keyword ranking (Postgres websearch
  full-text over a per-product text card + trigram similarity for typos) and semantic ranking (pgvector
  cosine kNN) are fused with Reciprocal Rank Fusion. `sort` defaults to `relevance` when `q` is present.
  `GET /search/suggest?q=` feeds the header typeahead. Every search is logged with its result count
  (`discovery_productevent`, kind=search) — zero-result queries tell you what the catalogue is missing.
- **Recommendations** — `/products/{slug}/similar` (embedding neighbours), `/products/{slug}/frequently-bought-together`
  (order co-occurrence), `/recommendations` (taste-profile vector from views/wishlist/cart/purchases; falls
  back to trending). Behaviour is recorded server-side from the existing endpoints; guests via `X-Cart-Token`.
- **Embeddings** — `EMBEDDING_PROVIDER=hash` (default) is offline and lexical: no key, deterministic, good
  for dev/tests, but it cannot match *meaning* ("tinted moisturiser" → BB cream needs a real model).
  Set `voyage` (+`VOYAGE_API_KEY`) or `openai` (+`OPENAI_API_KEY`) and run `embed_products --all`.
  `EMBEDDING_DIMENSIONS` (512) is baked into the pgvector column — changing it needs a migration.
- **SQLite** — everything still works without Postgres (vectors stored as text, similarity computed in
  Python, substring keyword match), so `manage.py test` runs anywhere; the Postgres-only paths are covered
  by running the suite inside the container.

## Conventions

- UUID primary keys, `created_at` / `updated_at` everywhere (`core.models.TimeStampedModel`); soft delete via `SoftDeleteModel` where the design implies history (users' addresses, cards, products, reviews).
- Money as integer minor units (`*_cents`) + `currency`.
- PII that must never be queried in bulk (ID/passport numbers, bank account numbers) uses `core.fields.EncryptedTextField` (Fernet, keyed by `FIELD_ENCRYPTION_KEY`); a plain `last4` column is kept where the UI needs to display something.
- Card PAN / CVV are never stored — only a PSP token + brand + last4.

## API layer conventions (django-ninja)

Every app is split into three files so sprint-to-sprint changes land in one place:

| file | owns | changes when… |
|---|---|---|
| `<app>/schemas.py` | request/response shapes + field validation | the UI form changes |
| `<app>/services.py` | business rules (uniqueness, state machines, side effects) | a flow/rule changes |
| `<app>/api.py` | `Router`s — thin: parse → call service → return | an endpoint is added/renamed |

Rules:
- **Schemas**: `XIn` (create, all required fields required), `XPatchIn` (every field `Optional`, view applies `payload.dict(exclude_unset=True)` so *omitted ≠ null*), `XOut` (response, explicit `fields` list — never `"__all__"`). Reusable field types (`PhoneStr`, `EmailStr`, `PasswordStr`, `OtpCode`, `CountryCode`, …) live in `core/validators.py` — change a rule once, every schema follows.
- **Enums** in schemas reference the model `TextChoices` directly (`Address.AddressType`). Inner `TextChoices` classes must have **globally unique names** (`OrderStatus`, not `Status`) — Ninja flattens them into OpenAPI components by bare class name and silently overwrites collisions.
- **Errors**: business errors raise `ninja.errors.HttpError(status, detail)`; validation errors are flattened by `asknget/api.py` to `{"detail": "...", "errors": [{"field", "message"}]}` (422). Declare every non-200 status in `response={...}` so it shows in Swagger.
- **Auth**: `core.auth.JWTAuth` (Bearer access token, 15 min) sets `request.auth` to the `User`. Refresh tokens are opaque, hashed in DB, rotated on every use. `OptionalJWTAuth` for guest-or-user endpoints.
- **`None` → `""`**: services pass optional payload fields through `core.models.coerce_nulls(Model, data)` before hitting non-nullable `CharField`s.
- **Tests** live in `<app>/tests.py` and hit the real URLconf (`/api/v1/...`) so schema, service and router are exercised together. Run: `uv run python manage.py test`.
- Versioning: everything is mounted under `/api/v1/`; a breaking contract change means a `v2` router, not an edit.

Mounted routers: `auth` (`/api/v1/auth`), `me` (`/api/v1/me`). Next: `catalog`, `cart`/`orders`, `sellers`, `marketing`.


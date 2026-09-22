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
- Data model: [`../docs/data-model.md`](../docs/data-model.md) · Sprint plans and decisions: [`../docs/sprint-2-plan.md`](../docs/sprint-2-plan.md), [`../docs/sprint-3-plan.md`](../docs/sprint-3-plan.md), [`../docs/sprint-4-plan.md`](../docs/sprint-4-plan.md) (markets), [`../docs/sprint-5-plan.md`](../docs/sprint-5-plan.md) (premium sellers) · Live API docs: `/api/v1/docs`.

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

Two images: the API (`backend/Dockerfile`, built on Docker Hub's official `astral/uv` image so dependencies are installed by uv from `uv.lock`, and `uv` ships in the container) and `pgvector/pgvector:pg17` (official Postgres 17 + the pgvector extension used by search/recommendations). The API image runs twice: `web` (gunicorn) and `worker` (background jobs — today it keeps product embeddings fresh). In front sits `proxy` (`nginx:alpine`): it serves `/media/` straight from the uploads volume, gzips and buffers API responses, and is the only published port (`HTTP_PORT`, default 80).
Your code stays where it is — the image *copies* it at build time, and in dev mode the
working tree is *bind-mounted* into the container so edits reload live.

```bash
cp backend/.env.example backend/.env    # fill SECRET_KEY, FIELD_ENCRYPTION_KEY, POSTGRES_PASSWORD (+ same password in DATABASE_URL)
docker compose up -d --build            # dev: Postgres + runserver with live reload on :8000 (nginx on :80 as well)
docker compose exec web python manage.py seed_demo   # optional demo data
```

| I want to… | Command |
|---|---|
| Start / stop (data kept) | `docker compose up -d` · `docker compose down` |
| Start **production-shaped** (nginx → gunicorn, no bind mount) | `docker compose -f compose.yaml up -d --build` → http://localhost (or `HTTP_PORT`) |
| Port 80 not allowed on your PC (Windows reserves it) | put `HTTP_PORT=8080` in a **root** `.env` (compose variable, git-ignored) |
| Size gunicorn | `WEB_CONCURRENCY=5` / `GUNICORN_CMD_ARGS=--threads 2` in `backend/.env` (defaults 3 × 2) |
| Reclaim Docker disk after many rebuilds | `docker builder prune -af` (build cache), `docker image prune` |
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
- Static files (admin, Swagger) are baked into the image and served by whitenoise through the proxy;
  uploads are served by nginx from the `media` volume (`deploy/nginx.conf`). In dev (`SERVE_MEDIA=1`)
  Django also serves them on :8000 so the bind-mounted `backend/asknget/media` works without the proxy.
- The prod stack already sets `BEHIND_PROXY=1`; for HTTPS terminate TLS at nginx (or a cloud LB), set
  `CSRF_TRUSTED_ORIGINS=https://your.domain`, `DEBUG=0`, and put your domain **and its country subdomains**
  (`asknget.com,.asknget.com`) in `ALLOWED_HOSTS` — `za.asknget.com` is how the API knows the market.

## Markets: countries, language, timezone (`markets` app)

A **country is a market**: it has its own currency, timezone, languages, KYC policy and return policy
(all edited in Django admin → *Markets*), and every store — hence every product, cart and order — belongs
to exactly one. Buyers only see their market's stores. Details and the reasons behind each choice:
`docs/sprint-4-plan.md`.

- **Resolution per request** (`markets.middleware.RegionMiddleware`, `/api/` only):
  country = `X-Region` / `?region=` → host subdomain (`za.asknget.com`) → default (`DEFAULT_COUNTRY`);
  language = `X-Language` / `?lang=` → `Accept-Language` within the country's list → country default;
  timezone = `X-Timezone` → country timezone. Unknown explicit region → `400 unknown_region`. The response
  carries `X-Region` and `Content-Language`. Everything is reset after the response.
- **In code**: `markets.context.get_country()` is the active market (**None outside a request** = unscoped);
  `catalog.services.public_products()` filters by it, so every storefront query, search and recommendation
  rail is market-scoped in one place. Jobs and tests that need one market use `with markets.context.region("PK"):`.
  The seller portal switches to the **store's** market (`portal.auth.StoreAuth`).
- **Endpoints**: `GET /regions` (switcher), `GET /regions/current` (resolved market + localised return policy),
  `GET /me` → stored `language` + resolved `locale`, `PATCH /me {language}`.
- **Rules**: one market per cart (`409 cart_region_mismatch`; `DELETE /cart/items` to clear and switch),
  delivery methods / vouchers / banners per market, checkout refuses another country's address
  (`409 ships_outside_region`), `Order.country` + `return_window_days` are snapshots.
- **KYC per country**: `sellers/policy.py` overlays the country's `KycPolicy` (documents, labels such as
  CNIC / SA ID, ID-number regex, instructions) on the defaults in `sellers/rules.py`; the wizard reads it
  from `ApplicationOut.policy` or `GET /lookups/seller?country=`.
- **Seeds**: `seed_lookups` creates ZA and PK (+ delivery methods per market); `seed_demo` adds a PK demo
  store so `X-Region: PK` shows a different catalogue. Add a country in admin, then re-run `seed_lookups`
  for its delivery methods (or add them in admin).

## Seller portal (`portal` app)

Everything under `/api/v1/seller/*` (except onboarding, which lives in `sellers`) needs a seller **with an
active store** — `StoreAuth` sets `request.store` and returns `403 no_active_store` otherwise. Every number is
derived per store from orders / products / events; the contract for each screen (definitions of Sales vs
Revenue, customer types, fulfilment states, what the design left undefined) is `docs/sprint-3-plan.md`.

- **Store orders** — a buyer order can span stores. Each seller sees only their lines; their fulfilment status
  is `OrderItem.status` (`pending → processing → packed → shipped → delivered`, `cancelled`, `returned`) and
  the buyer-facing `Order.status` is derived from the parts (`orders.services.derive_order_status`).
- **Stock** — `catalog.services.adjust_stock` is the only write path (checkout, cancel, return, manual);
  it logs a `StockMovement` and refreshes the derived `Product.stock_status` (in / low / out; `preorder` is manual).
- **Products** — one `PATCH` for every wizard step plus sub-resources (images, options → variants, content).
  Sellers submit → `pending`; staff approve/reject at `/admin/products`.
- **Customers** are computed from orders (key = user id or guest email) and cached per store for 60 s; the
  cache key changes whenever the store's order lines change.

## Seller plans and chat (premium — `portal.entitlements`, `chat` app)

A store is **standard** or **premium** (`Store.is_premium`, staff toggle in Django admin or
`POST /admin/stores/{id}/plan`). Premium unlocks two things; the contract and the reasoning are in
`docs/sprint-5-plan.md`.

- **Entitlements** — `portal/entitlements.py` is the only place that reads the flag: `plan_of(store)`,
  `has(store, feature)`, `require(store, feature)` → `403 premium_required`. Features (`analytics_advanced`,
  `chat_inbox`) carry their own label and description, so `GET /seller/plan` (also embedded in
  `GET /seller/dashboard`) renders the sidebar locks and the upgrade card without hard-coded copy.
  When real subscriptions arrive, only `plan_of()` changes.
- **Advanced Analytics** — `/seller/analytics/{overview,funnel,products,search,customers,fulfilment}`
  (+ `export.csv`), all `?period=`. They answer *why*, not *how much*: funnel drop-off, slow movers,
  the queries shoppers typed before finding you, repeat rate, hours from payment to shipped and from shipped to delivered. Everything is
  derived from `ProductEvent` / `OrderItem` / `OrderStatusEvent` — no new tracking. The search report is
  the only non-aggregate: it scans a capped window of events (`ANALYTICS_EVENT_SCAN_LIMIT`) and caches 5 min.
- **Chat** — one `Conversation` per (store, customer) plus append-only `Message`s. Buyers (signed in)
  may write to any active store **in their market** and always see `seller_can_reply`; the inbox
  (`/seller/chat`) is premium, but `GET /seller/chat/summary` works on every plan so a standard seller
  knows messages are waiting. Clients poll `GET …/messages?after=<message id>` — a timestamp cursor would
  re-deliver its own anchor, because responses round datetimes to milliseconds. Unread counters are
  denormalised and written only by `chat.services.send()`. Messages that look like off-platform contact
  details are flagged for staff (`GET /admin/chat/flagged`), never blocked; 30 messages/minute per user.

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

## Performance guard rails

- **N+1 detector** — `django-zeal` is active under `manage.py test` (and in a dev server with `ZEAL=1`):
  any lazy relation load inside a loop raises `NPlusOneError`, so a missing `select_related` /
  `prefetch_related` fails the suite. Allow a legitimate case with `zeal_ignore([...])`, never by
  disabling the detector.
- **Query budgets** — `core/test_query_budgets.py` pins the SQL query count of every hot endpoint
  against a > 1-row fixture. Adding a query is fine when a feature needs it: bump the number in the
  same commit. A number that grows with the rows is a bug.
- **Write paths too** — lock stock rows in bulk (`catalog.services.lock_variants`) and pass
  `locked=True` to `adjust_stock` inside loops; create M2M links through the `through` model with
  `bulk_create` rather than `.set()` per object.
- Baselines and the wider practice list: `docs/engineering-practices.md`.

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


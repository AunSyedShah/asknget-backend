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
│   ├── orders/           # cart, vouchers, per-store delivery, orders, status timeline, payments
│   └── marketing/        # banners, newsletter, contact messages
├── .env.example
└── pyproject.toml        # managed with uv
```

## Documentation

- **Developer guide (PDF)** for frontend/stakeholders: [`../docs/asknget-backend-developer-guide.pdf`](../docs/asknget-backend-developer-guide.pdf) — regenerate after API changes:
  `uv run --with reportlab python ../docs/build_developer_guide.py` (from `backend/`).
- Working on the frontend repo? Start with [`../docs/frontend-handoff.md`](../docs/frontend-handoff.md): the API contract, the Figma tooling and the endpoint index.
- Where the backend stands against the current design: [`../docs/design-sync-audit.md`](../docs/design-sync-audit.md).
- Environment files and every variable: [`../docs/environment.md`](../docs/environment.md).
- Design drift: `python docs/figma/snapshot.py --check` before touching a screen. If the full snapshot is
  rate-limited (Starter plan), `--index` lists the frames, `--nodes --only "<frame>"` snapshots a few, and
  `--render "<frame>"` saves a PNG — three separate budgets, so one of them is usually open.
  If all three are cold, `uv run --with zstandard python docs/figma/snapshot.py --from-fig <file.fig>`
  reads a local export offline (Figma → File → Save local copy). It is additive: a .fig cannot expand
  component instances, so it fills in missing screens rather than overwriting richer API-derived ones.
- Data model: [`../docs/data-model.md`](../docs/data-model.md) · Sprint plans and decisions: [`../docs/sprint-2-plan.md`](../docs/sprint-2-plan.md), [`../docs/sprint-3-plan.md`](../docs/sprint-3-plan.md), [`../docs/sprint-4-plan.md`](../docs/sprint-4-plan.md) (markets), [`../docs/sprint-5-plan.md`](../docs/sprint-5-plan.md) (premium sellers), [`../docs/sprint-6-plan.md`](../docs/sprint-6-plan.md) (analytics + seller-defined shipping), [`../docs/sprint-7-plan.md`](../docs/sprint-7-plan.md) (admin-managed config, team, transactions, notifications) · Live API docs: `/api/v1/docs`.

## Setup

```bash
uv sync
cp .env.example .env            # includes the shared dev FIELD_ENCRYPTION_KEY; keep it
uv run poe seed                 # migrate + seed_lookups + seed_demo (password DemoPass123!)
uv run poe dev                  # http://127.0.0.1:8000
```

- API docs (Swagger): http://127.0.0.1:8000/api/v1/docs · OpenAPI JSON: /api/v1/openapi.json
- Admin: http://127.0.0.1:8000/admin/

### Tasks (`uv run poe …`)

`manage.py` lives in `backend/asknget/` while `pyproject.toml` lives in `backend/`, and `backend/.env`
points `DATABASE_URL` at the **Docker** host `db` — so a raw local command was
`cd asknget && DATABASE_URL=sqlite:///db.sqlite3 uv run python manage.py test`. Each task below sets the
working directory and the local SQLite URL for you, and works from anywhere under `backend/`.

| Task | Does |
|---|---|
| `poe dev` | dev server on :8000 (local SQLite) · `poe zeal` = same with the N+1 detector armed |
| `poe test` | whole suite · `poe test chat portal.test_analytics` for labels |
| `poe check` | `manage.py check` + missing-migration check — what CI should run |
| `poe migrate` · `poe makemigrations [app]` · `poe seed` · `poe shell` | the usual |
| `poe manage <anything>` | any other command, e.g. `poe manage createsuperuser` |
| `poe up` · `poe down` · `poe logs [service]` | the Docker stack |
| `poe dtest [labels]` · `poe dmanage <cmd>` | run inside the container against Postgres |
| `poe guide` · `poe figma` | rebuild the developer guide PDF · check the design for drift |

`uv run poe` (no task) lists them. `uv tool install poethepoet` once, and you can drop the prefix and
just type `poe test`. Tasks are defined at the bottom of `pyproject.toml`; poethepoet is a dev-only
dependency, so the Docker build (`uv sync --no-dev`) never installs it. uv has no built-in task runner
([astral-sh/uv#5903](https://github.com/astral-sh/uv/issues/5903)).

Without `DATABASE_URL` the project falls back to SQLite; set it to a Postgres URL for anything beyond local experiments.

### Cross-origin frontends (CORS)

Needed only when the storefront is on a different origin than the API — set `CORS_ALLOWED_ORIGINS` (or
`CORS_ALLOWED_ORIGIN_REGEXES` for country subdomains) in `backend/.env`; empty means no CORS headers at
all, and `DEBUG=1` already allows any `localhost` port. The allowed **request** headers include
`X-Cart-Token`, `X-Region`, `X-Language` and `X-Timezone`, and `X-Region` / `Content-Language` /
`Content-Disposition` are **exposed** so a cross-origin page can actually read them — both are pinned by
`core/test_cors.py`. Credentials are off by design: auth is a Bearer header, not cookies.

### Environment files

Two of them: the **repo root** `.env` configures Docker Compose (`HTTP_PORT`), and **`backend/.env`**
configures Django and every container. Both have a committed `.env.example`; neither is ever committed.
Every variable is documented in [`../docs/environment.md`](../docs/environment.md).

## Running with Docker

Two images: the API (`backend/Dockerfile`, built on Docker Hub's official `astral/uv` image so dependencies are installed by uv from `uv.lock`, and `uv` ships in the container) and `pgvector/pgvector:pg17` (official Postgres 17 + the pgvector extension used by search/recommendations). The API image runs twice: `web` (gunicorn) and `worker` (background jobs — today it keeps product embeddings fresh). In front sits `proxy` (`nginx:alpine`): it serves `/media/` straight from the uploads volume, gzips and buffers API responses, and is the only published port (`HTTP_PORT`, default 80).
Your code stays where it is — the image *copies* it at build time, and in dev mode the
working tree is *bind-mounted* into the container so edits reload live.

```bash
cp backend/.env.example backend/.env    # fill SECRET_KEY, POSTGRES_PASSWORD (+ same password in DATABASE_URL); keep the shared dev FIELD_ENCRYPTION_KEY
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
  vouchers / banners per market (shipping is the seller's, see below), checkout refuses another country's address
  (`409 ships_outside_region`), `Order.country` + `return_window_days` are snapshots.
- **KYC per country**: `sellers/policy.py` overlays the country's `KycPolicy` (documents, labels such as
  CNIC / SA ID, ID-number regex, instructions) on the defaults in `sellers/rules.py`; the wizard reads it
  from `ApplicationOut.policy` or `GET /lookups/seller?country=`.
- **Seeds**: `seed_lookups` creates ZA and PK; `seed_demo` adds a PK demo store so `X-Region: PK` shows a
  different catalogue. A new country needs nothing for shipping — each store brings its own options.

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

## Shipping is the seller's (`sellers.StoreDeliveryOption`)

The platform's `orders.DeliveryMethod` is **deprecated**: rows stay, nothing writes them. Sellers define their
own options and the buyer picks one **per store**. `docs/sprint-6-plan.md` §3 has the contract.

- **Options** — `StoreDeliveryOption` (name, free-text `eta_label`, `price_cents`, `kind` delivery/pickup)
  with `GET/POST/PATCH/DELETE /seller/delivery-options`. Every store starts with the design's three
  (`sellers.services.ensure_default_delivery_options`); deleting the last active one is a `409`, and one already
  used by an order is deactivated rather than deleted so history still resolves.
- **Per product** — `Product.delivery_options` (M2M, `PUT /seller/products/{id}/delivery-options`).
  **Empty means all of the store's active options**, so nothing had to be backfilled.
- **Cart** — `GET /cart` returns one `deliveries[]` group per store: the options *every* line in that group
  supports (intersection), which is selected, and its price. Selections live on `Cart.delivery_selections`
  (`{store_id: option_id}`) and default to the cheapest **delivery** option — never pickup, which is usually
  free and would silently ask the buyer to collect. `PATCH /cart/delivery {store_id, option_id}` changes one.
  A store whose lines share no option blocks checkout (`can_checkout: false`, then `409`).
- **Order** — `POST /checkout` takes optional `deliveries: [{store_id, option_id}]` and snapshots one
  `OrderDelivery` per store. `Order.shipping_cents` is still the total, so totals, invoices and exports are
  unchanged.

## Seller plans and chat (premium — `portal.entitlements`, `chat` app)

A store is **standard** or **premium** (`Store.is_premium`, staff toggle in Django admin or
`POST /admin/stores/{id}/plan`). Premium unlocks two things; the contract and the reasoning are in
`docs/sprint-5-plan.md`.

- **Entitlements** — `portal/entitlements.py` is the only place that reads the flag: `plan_of(store)`,
  `has(store, feature)`, `require(store, feature)` → `403 premium_required`. Features (`analytics_advanced`,
  `chat_inbox`) carry their own label and description, so `GET /seller/plan` (also embedded in
  `GET /seller/dashboard`) renders the sidebar locks and the upgrade card without hard-coded copy.
  When real subscriptions arrive, only `plan_of()` changes.
- **Advanced Analytics** — `/seller/analytics/{overview,sales,funnel,products,search,customers,fulfilment}`
  (+ `export.csv`), all `?period=`. `overview` and `sales` are the design's two analytics screens (daily
  series, sales by category, top products, customer mix; then gross → discounts → refunds → net with
  `?bucket=daily|weekly|monthly` and Sales by Channel from `Order.channel`). **Two "net"s coexist on purpose**:
  `overview.net_sales_cents` is gross minus cancelled/returned, `sales.net_sales_cents` is the designer's
  gross minus discounts, refunds and adjustments — the last two are 0 until the PSP lands.
  An order-level voucher is split across its stores pro-rata by subtotal share
  (`orders.services.store_discount_cents`, largest-remainder rounded). They answer *why*, not *how much*: funnel drop-off, slow movers,
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

## Admin-managed configuration (`config` app)

Principle (sprint 7): **anything a business person might change is data an admin edits in Django admin**, not a
constant. Contract and reasoning: `docs/sprint-7-plan.md` §1.

- **Settings** — a typed registry of scalar tunables (`config/definitions.py`: type, bounds, choices, allowed scopes,
  default). An override is a `Setting` row at one scope — store, market, or global — and the most specific wins, then
  the code default. `from config import services as config; config.get("payouts.hold_days", store=store)`; `get_many`
  is one query. No cache, so an edit applies on the next request. Every save is validated against the definition.
  Add a tunable by registering it, nothing else. `manage.py show_settings [--country PK | --store <slug>]` prints each
  effective value and where it came from.
- **Commission** — `CommissionRule`: store / category (nearest parent counts) / market / global, effective-dated so a
  rate can be scheduled; each posted fee records the rate and rule it used, so changing a rate never rewrites history.
- Also data: `TeamRole` presets, `NotificationTemplate` wording (per language), and the markets' policies from sprint 4.

## Team and permissions (`team` app)

A store has its **owner** (implicit: full access, cannot be removed) and **members**. Access is `none` / `view` /
`manage` in each of eight sections (`dashboard, orders, products, inventory, customers, marketing, analytics,
transactions`); reading needs `view`, anything that changes state needs `manage`.

- `StoreAuth(section="orders")` guards a router; `StoreAuth(owner_only=True)` the team itself; plain `StoreAuth()` is any
  member. `403 permission_denied` is the permission answer; `403 premium_required` stays the plan answer. The store
  is the one you own, else one you are an active member of; **`X-Store: <id>`** chooses between several and never falls
  back to a store you didn't ask for. The owner path is still one query.
- **Roles are rows** (`TeamRole`): choosing one *copies* its levels, so editing a role never widens anyone already in it.
- **Invitations** — `POST /seller/team/invites`, an e-mailed link (`PORTAL_URL`), token stored only as a hash, lifetime
  and seat limit are admin settings. `POST /auth/team/accept` signs in an existing account or creates one.
- `GET /seller/me` says who is working, in which store, and which sections to show in the sidebar.
- **Recent Activity** — actions that matter (price changes, status moves, stock, order moves, exports, delivery options,
  team changes) are written to `StoreActivity` with `team.services.log(...)`; autosave is deliberately not logged.

## Transactions — the ledger (`ledger` app)

Append-only, per store; every balance is computed from it. Full rules: `docs/data-model.md` §7e.

- **Payment confirmed** (`orders.services.on_payment_confirmed`) posts per store a `sale` (items − its voucher share + its
  delivery charge — the stores' sales add up to the buyer's total to the cent) and a `fee` (commission per line).
- **Delivery** starts the hold; `payouts.hold_mode` (`return_window` / `fixed_days` / `none`, per market or store) decides
  when it becomes *available*. **Cancel / return / refund** posts a `refund` and `fee_refund`, once per store.
- Idempotent in the database (one posting of each kind per order and store; the sign of each type is a constraint).
- **Staff move money, sellers only read it**: payouts and adjustments through `/admin/payouts`, `/admin/ledger` or Django
  admin, via the same service functions. A payout can't exceed the available balance or fall below `payouts.minimum_cents`.
- `manage.py backfill_ledger [--dry-run]` posts orders paid before the ledger existed, whole and dated when they happened.
- The money events live in one place each in `orders.services` (`on_payment_confirmed`, `on_goods_delivered`,
  `on_order_money_returned`, `on_order_cancelled`) — add new reactions there, not in the views.

## Notifications (`notifications` app)

The event and its `params` are stored; the **sentence is rendered when read**, in the reader's language, from a
`NotificationTemplate` an admin can reword, translate or switch off (English required, others optional). A person sees a
category only if they can view its section; read state is per person. Emitters (`notifications.services.order_paid`,
`stock_alert`, `payout_processed`, …) are called from the money-event hooks above, in the same transaction as the event.
Low stock fires once per *crossing*, not on every sale below the line.

## Returns (`returns` app)

A buyer asks to return delivered items from one store's part of an order; the seller approves or rejects, the buyer ships it back, the seller
receives it, and the ledger refunds. Plan, decisions and what was left open: `docs/returns-plan.md`; tables: `docs/data-model.md` §7f.

- **Rules are data**: `markets.ReturnPolicy` (days for change of mind / defects, restocking fee, wording), `ReturnReason`, and per-category `ReturnRule`
  overrides (`0` days = not accepted). Edit them in Django admin or via `/admin/returns/{policies,reasons,rules}`. `returns.policy.terms_for` resolves them and
  checkout snapshots the result onto each `OrderItem.return_terms`, so editing a rule never rewrites an existing order.
- **One workflow module**: every change goes through `returns.services` (it writes the timeline, restocks, calls `ledger.refund_items` and notifies), never
  straight on the model. Statuses: requested, approved, rejected, escalated, received, refunded, cancelled.
- **Money**: `ledger.refund_items` refunds the returned value and the matching share of the commission, once per request; `ledger.refund` (whole orders) only
  pays what has not already gone back. A goodwill "returned" by a seller or staff is `409 return_in_progress` while a customer request for those lines is open.
- **Courier slot**: `returns/couriers/` is where the technical lead's courier API plugs in. Subclass `Courier` (`book_return`, optionally `cancel`, `track`,
  `parse_webhook`), `register()` it, add its import to `couriers.load()`, then set the admin setting `returns.courier` to its slug (per market or globally).
  Until then the `manual` courier is used and the buyer types the tracking number. Webhooks arrive at `POST /couriers/{slug}/webhook`; polling is
  `manage.py sync_return_tracking`. A courier failing never blocks an approval.
- **Settings** (`manage.py show_settings` lists each effective value): `returns.courier`, `returns.auto_approve`, `returns.seller_response_days`, `returns.max_photos`, `returns.allow_escalation`.
- **Not connected yet**: a payment provider (the buyer's refund stays `pending` until staff mark it paid) and the courier itself.
- **Transitions lock the request row** (`returns.services._locked`) and talk to the courier / send e-mail only **after commit**
  (`transaction.on_commit`). Keep both when adding a step. Tests use `CommitClient` to run those callbacks.

## Uploads

Every upload goes through `core.uploads`: images are opened with Pillow and stored as `<random>.<detected format>`, other files get the
extension of an allow-listed content type. The client's file name is display text only. nginx serves `/media/` with `nosniff` and refuses
`/media/seller_documents/`: seller documents are reachable only through signed, expiring links (`sellers.files`, `SIGNED_FILE_TTL_SECONDS`).

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


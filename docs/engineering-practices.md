# Engineering practices — architecture, query optimisation, N+1 prevention

Researched 2026-09-20 against the current Django / Postgres / API ecosystem, then audited
against this repo. Each section: **the standard practice → where asknget stands → action.**
Sources at the end.

Load-test baseline (laptop, nginx → gunicorn 3×2 → Postgres, 20 concurrent): health 421 req/s,
product list 65 req/s (31 ms/req, 3 queries), search 35 req/s (77 ms, 8 queries), PDP 32 req/s
(53 ms, 9 queries), seller dashboard 14 req/s (114 ms, 27 queries). CPU-bound in Python, no N+1.

---

## 1. Architecture (how the code is organised)

**Standard.** The most widely adopted Django convention for non-trivial apps is the HackSoft
style guide: thin views/API layer → `services.py` for writes and business rules → `selectors.py`
for reads with optimised querysets → models with constraints, no business logic in serializers.
Input and output schemas are separate per endpoint. Rules that change with the product live as
data, not scattered `if`s.

**asknget.** Already this shape: every app has `schemas.py` / `services.py` / `api.py` / `tests.py`;
`sellers/rules.py` holds design-driven rules as data; `portal/*.py` is split per screen. Two gaps:
reads and writes share `services.py` (no `selectors.py`), and a few "reusable queryset builders"
live next to write logic (`catalog.services.public_products`, `portal.orders.store_orders`).

**Action.** Low priority. When a module passes ~400 lines, split `selectors.py` out of it; do not
refactor for its own sake. Keep the rule: **API functions never build querysets** — they call a
selector/service and serialise.

## 2. Preventing N+1 queries

**Standard.**
- `select_related` for FK/O2O (SQL JOIN), `prefetch_related` (+ `Prefetch(queryset=…)`) for
  reverse FK / M2M; never touch a relation in a loop that the queryset did not load.
- Aggregates via `annotate` / `Subquery` / `Exists` instead of Python loops; avoid multiple
  multi-valued JOINs in one `annotate` (row explosion) — use subqueries per aggregate.
- **Guard rails in CI**: `assertNumQueries` (or `django_assert_max_num_queries`) on every list
  endpoint test, and an N+1 detector that *fails tests* — `django-zeal` (the maintained successor
  of `nplusone`) raises on lazy loads inside loops; `django-silk` / debug-toolbar for interactive
  profiling in dev.

**asknget.** 40 `select_related`/`prefetch_related` sites, 36 `annotate`/`Subquery` sites; the
row-explosion trap is avoided in `portal.products.store_products` (three separate subqueries).
Measured counts are flat (3–9 queries per page; dashboard 27 across 8 widgets). **But nothing
enforces this**: zero `assertNumQueries`, no detector. The next developer can reintroduce an N+1
and the suite stays green.

**Action (high value, ~1 h).**
1. Add `django-zeal` to dev deps; enable in test settings so any lazy load in a loop fails the suite.
2. Add `assertNumQueries` to the list/detail tests of catalog, orders, portal (fixed upper bounds).
3. Add `django-silk` to the dev override only (never in the image) for ad-hoc profiling.

## 3. Query-level optimisation

**Standard.**
- Fetch only what you serialise: `.only()` / `.defer()` for wide rows (large text columns),
  `.values()`/`.values_list()` for lists that don't need model instances (skips model + pydantic
  instantiation — the biggest CPU cost in list endpoints).
- `exists()` not `count()` for booleans; `count()` not `len()`; `update()`/`F()` for counters;
  `bulk_create`/`bulk_update`; `iterator(chunk_size=…)` for big scans.
- Indexes follow the WHERE/ORDER BY of real queries: composite indexes in filter order, partial
  indexes for hot subsets (`WHERE status='active'`), GIN for full-text/JSON/arrays, HNSW for vectors.
- Measure with `EXPLAIN (ANALYZE, BUFFERS)` and keep `pg_stat_statements` on in every Postgres so
  the slowest queries are a `SELECT` away.
- Keyset (cursor) pagination for deep/large or fast-changing lists; offset is fine for numbered
  pages over < ~10k rows.

**asknget.** 24 declared indexes incl. GIN full-text, trigram and HNSW; `F()` counters; `iterator`
in embedding; `update()` for denormalised fields. Product list serialises 12 model instances through
nested pydantic schemas (~30 ms CPU). Pagination is offset (`PagePagination`) — matches the design's
numbered pager and the catalogue size. `pg_stat_statements` is **not** enabled.

**Action.**
- Enable `pg_stat_statements` in `compose.yaml` (`command: postgres -c shared_preload_libraries=pg_stat_statements`)
  + `CREATE EXTENSION` in a migration — zero cost, permanent visibility. *(15 min)*
- Add a partial index `Product(store_id, updated_at) WHERE deleted_at IS NULL` and
  `Order(placed_at)` once seller tables exceed a few thousand rows; not before (measure first).
- Keep offset pagination; switch `/seller/orders` and `/seller/customers` to keyset if a store
  passes ~50k orders.
- If the product list becomes the bottleneck, serialise cards from `.values()` rows instead of
  model instances (≈3× cheaper) — only after caching (§4), which removes most of the load anyway.

## 4. Caching

**Standard.** Cache at the highest layer that is still correct: per-view/response for anonymous
read-heavy endpoints (short TTL, vary on query string and auth), per-object/fragment for expensive
aggregates (KPIs), low-level `cache.get_or_set` for computed values; invalidate by short TTL or
version keys, not by trying to track every write. Django ships a Redis backend; use it in
production so all worker processes share one cache. LocMem is per-process (3 workers = 3 caches).

**asknget.** Django default LocMem; three ad-hoc caches (frequently-bought-together 10 min,
customers rollup 60 s with a self-invalidating key, hash-embedder instance). No response caching.
Dashboard recomputes 27 queries per refresh.

**Action (high value).**
- Per-store cache of `/seller/dashboard`, `/seller/orders/overview`, `/seller/customers/overview`
  and the KPI endpoints for 60 s — turns 114 ms into ~3 ms. *(30 min)*
- Anonymous response cache (30–60 s, keyed by full URL, skipped when `Authorization` or
  `X-Cart-Token` is present) for `/products`, `/products/filters`, `/categories`, `/banners`,
  `/recommendations` (trending path). *(1 h)*
- Backend: start with Django's **database cache** (`createcachetable`) — no new container, shared
  across workers, plenty for 60 s TTLs at this scale; move to **Redis** when hit rates matter
  (it is also the natural broker if the task queue ever outgrows the DB backend).

## 5. Work that should not run in a request

**Standard.** Anything slow, retryable or not user-visible goes to a background job: emails,
webhooks, embeddings, image resizing, exports, denormalised counters, analytics rollups.
Idempotent jobs, retries with backoff, dead-letter visibility.

**asknget.** Order email is sent inline in checkout; embeddings via a polling loop; view events are
a synchronous insert per PDP request. Plan agreed: Django 6 Tasks framework + Postgres-backed
worker (see conversation of 2026-09-20 / sprint plan).

**Action.** Implement with the payments sprint (first webhook job) or earlier if emails start failing.

## 6. Connection handling & server sizing

**Standard.** Connection pool per process; `CONN_MAX_AGE` **or** pool, not both; gunicorn workers
≈ 2×CPU+1 for sync apps, threads 2–4 for I/O-heavy endpoints; a reverse proxy buffers slow
clients and serves files; health checks and graceful timeouts.

**asknget.** Done: psycopg pool (`DB_POOL=1`), nginx edge (media, gzip, buffering), `WEB_CONCURRENCY`
/ `GUNICORN_CMD_ARGS` env-driven, health checks on every service.

## 7. Observability

**Standard.** Request tracing with SQL spans (OpenTelemetry or Sentry Performance), slow-query log
(`log_min_duration_statement`), error tracking, structured access logs; a `/health` that checks the
DB, not just the process.

**asknget.** Access logs only; `/health` returns 200 without touching the DB.

**Action.** `/health` → also `SELECT 1` (5 min). Add Sentry (free tier) before the first external
users; OpenTelemetry when there is more than one service.

## 8. Testing for performance

**Standard.** Query-count assertions in unit tests; a small repeatable load test (k6 / hey) with
recorded baselines in the repo; run it after changes that touch hot paths.

**asknget.** Baseline recorded above; script not yet in the repo.

**Action.** Add `scripts/loadtest.sh` (the `hey` invocation used for the baseline) and a
`docs/perf-baselines.md` table to append to. *(20 min)*

---

## Priority order

| # | Change | Effort | Effect |
|---|---|---|---|
| 1 | `django-zeal` in tests + `assertNumQueries` on list endpoints | 1 h | N+1 can no longer sneak in |
| 2 | 60 s cache on dashboard/overview/KPI endpoints (DB cache backend) | 1 h | seller pages 14 → ~300 req/s |
| 3 | Anonymous response cache on catalogue endpoints | 1 h | storefront 65 → several hundred req/s |
| 4 | `pg_stat_statements` on; `/health` checks DB | 20 min | permanent visibility |
| 5 | Load-test script + baselines file | 20 min | before/after numbers for every optimisation |
| 6 | Background tasks (Django Tasks + DB worker) | ½ day | emails/webhooks/embeddings off the request path |
| — | `selectors.py` split, keyset pagination, `.values()` serialisation, Redis | later | only when measurements say so |

## Sources

- Django docs — Database access optimization: https://docs.djangoproject.com/en/6.0/topics/db/optimization/
- Django docs — Cache framework: https://docs.djangoproject.com/en/5.1/topics/cache/
- HackSoft Django Styleguide (services/selectors): https://github.com/HackSoftware/Django-Styleguide
- django-zeal (N+1 detector, nplusone successor): https://github.com/taobojlen/django-zeal
- nplusone (original): https://pypi.org/project/nplusone/0.8.2
- django-check (static N+1 analysis): https://github.com/richardhapb/django-check
- Detecting N+1 with unit tests: https://www.valentinog.com/blog/n-plus-one/
- Counting queries with assertNumQueries (Vinta): https://www.vintasoftware.com/blog/counting-queries-basic-performance-testing-in-django
- Automating performance testing in Django (TestDriven): https://testdriven.io/blog/django-performance-testing/
- defer/only/exclude (TestDriven): https://testdriven.io/blog/django-query-optimization/
- 7 tricks for slow Django queries (Johnny Metz): https://johnnymetz.com/posts/slow-django-database-queries/
- Django caching strategies with Redis (2026): https://adhdecode.com/articles/django/django-caching-strategies-redis/
- Django Redis caching how-to (2026): https://oneuptime.com/blog/post/2026-01-27-django-redis-caching/view
- EXPLAIN ANALYZE (Crunchy Data): https://www.crunchydata.com/blog/get-started-with-explain-analyze
- PostgreSQL performance tuning 2026 (Codercops): https://blog.codercops.com/blog/postgresql-performance-tuning-2026
- pg_stat_statements for query analysis: https://chat2db.ai/resources/blog/analyze-query-performance-with-pg_stat_statements-in-postgresql
- API pagination: offset vs cursor vs keyset (Knit): https://getknit.dev/blog/api-pagination-best-practices/
- API pagination guide (Gusto): https://embedded.gusto.com/blog/api-pagination/
- django-ninja pagination docs: https://django-ninja.dev/guides/response/pagination/
- django-ninja pagination memory issue #1297: https://github.com/vitalik/django-ninja/issues/1297
- Django + Pydantic (TestDriven): https://testdriven.io/blog/django-and-pydantic/

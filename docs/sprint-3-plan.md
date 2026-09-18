# Sprint 3 — Seller Dashboard (Figma 2026-09-18, 10 new frames)

Field contract first, code second. Every number on these screens is **derived** from
orders / order items / products / events for **one store** — nothing below adds a stored
"total" column. Where the design is silent or inconsistent the decision is stated here and
mirrored as a question in `docs/data-model.md` §10 (Q20+).

Screens: Login · Seller Dashboard · Products (list) · Products (Add Product wizard) ·
Inventory · Orders Overview · Orders (list) · View Order · Customers Overview · Customers (list).
Sidebar entries **without** a screen — Returns, Chat, Analytics, Notifications, Team/Managers —
are out of scope (Q20).

## 1. Entities and relationships (the part the design cannot tell us)

| Concept on screen | What it actually is | Rule |
|---|---|---|
| "The seller" | `User.is_seller` **and** an `ACTIVE` `Store` they own | Every `/seller/*` dashboard route resolves `request.store`; 403 `no_active_store` before activation |
| "An order" (seller view) | the **subset of one buyer order's `OrderItem`s that belong to this store** — a *store order*, keyed `(order, store)` | A buyer order can span stores; each seller sees and fulfils only their lines. Price columns = sum of *their* line totals |
| Order status (seller) | **per-store fulfilment status**, stored on each `OrderItem.status` (all of a store's lines move together) | Buyer-facing `Order.status` is *derived* from its store parts after every seller update (§3) |
| Order timeline (seller) | `OrderStatusEvent` rows for that order **with `store` = this store** (new nullable FK) + the order-level Placed / Payment Confirmed events | Buyer's Track Order keeps seeing order-level events |
| "Customer" | a distinct buyer of this store: `Order.user` when signed in, else `Order.contact_email` | Derived from orders; no customer table. `New / Returning / One-time` are computed (§5) |
| "Product" statuses Active / Pending / Draft / Rejected / Archived | `Product.status` gains `pending` (submitted for review) and `rejected` (+ `rejection_reason`) | Sellers submit; staff approve/reject (§2) |
| "Stock" | `sum(variant.stock_qty)` for active variants | `Product.stock_status` becomes **derived** (out / low / in), refreshed on every variant/stock change; `preorder` stays a manual flag |
| "Low stock" | `0 < stock ≤ threshold` | `Product.low_stock_threshold` (nullable) → `Store.low_stock_threshold` (default 10) |
| "Inventory movements" | new `StockMovement` log: variant, delta, reason (`sale`, `cancel`, `manual`, `return`), note, actor | Every stock change goes through it; checkout/cancel included |
| "Add Note" on a customer | new `CustomerNote` (store, customer key, author, body) | Private to the store |

## 2. Products

**List** `GET /seller/products` — tabs = `status` (`all|active|pending|draft|rejected|archived`) with counts;
filters `q`, `brand`, `category`, `stock` (`in|low|out`); sort `updated|created|price|sold|name`. Row:
category, brand, `stock_qty`, `updated_at`, price, `sold_qty` (units in non-cancelled store orders),
`earned_cents` (their line totals), `status`, `stock_status`. The design's single "Status" column mixes
*Active / Out of Stock / Low Stock* — those are two different fields; we return both (Q22).

**KPIs** — `total` (not archived/deleted), `new_this_month` (created), `sold_qty_this_month`,
`returned_qty_this_month`. The card subtitles ("50% Average of total customers") are placeholder copy;
we return the month-over-month `change_pct` and the UI decides the sentence (Q21).

**Wizard** (5 steps, autosave like the seller application; each step `PATCH`):
1. Basic Info — `category` (subcategory = the leaf; we take one `category_id`, the UI shows two dropdowns from the tree), `brand_id`, `tags[]`, `name`, `short_description` ≤ 250. Long description / benefits / how-to-use sections stay optional fields (PDP needs them; wizard doesn't draw them — Q23).
2. Media — `POST /seller/products/{id}/images` (multipart, ≤ 8, first = primary), reorder, delete.
3. Pricing & Inventory — `price_cents`, `compare_at_price_cents` (> price), `options` (`{"Shade": ["Ivory","Natural"]}` → variants generated), per-variant `sku`, `price_cents`, `stock_qty`; `low_stock_threshold`.
4. Shipping — nothing is drawn. We store `weight_grams`, `length/width/height_mm`, `ships_from` (free text) and `handling_days` — the minimum a carrier quote will ever need (Q24).
5. Review — `POST /seller/products/{id}/submit` → `pending`; 422 lists what's missing (name, category, price, ≥1 image, ≥1 variant with stock).

**Actions** — `Edit` (PATCH steps, allowed in draft/rejected/active; editing an active product does *not* unpublish it), `Duplicate` → new draft copy "(copy)", `Archive`/`Unarchive`, `Delete` (soft, only draft/rejected/archived; active must be archived first), `Change Status` (active → draft "unpublish"; draft/rejected → submit), `View Analytics` (views from `ProductEvent`, sold, earned, conversion, 30-day series), `View Product` = public PDP slug.

**Admin review** — `GET /admin/products?status=pending`, `POST /admin/products/{id}/approve|reject {reason}`. Approve sets `active` + `published_at`. (Q25: is moderation wanted at all, or should sellers publish directly?)

## 3. Orders

Store-order statuses (per store, on `OrderItem.status`):
`pending` → `processing` → `packed` → `shipped` → `delivered`; `cancelled` from pending/processing;
`returned` from delivered (until the Returns flow is designed, seller marks it manually and stock is restored — Q26).
`pending` = paid/confirmed and waiting for the seller. Orders still `pending_payment` are **not shown** to
sellers (nothing to fulfil yet; PSP is next sprint) — Q27.

Derived buyer `Order.status` after each store update: all parts cancelled → `cancelled`; all delivered/returned
→ `delivered`; any shipped → `shipped`; any processing/packed → `processing`; else `confirmed`.
`Order.OrderStatus` gains `packed` and `returned` so both sides speak the same vocabulary.

Endpoints: `GET /seller/orders/overview` (KPIs total / pending / processing / delivered with change %, monthly
series delivered-pending-refunded, status share, latest 5), `GET /seller/orders` (tabs + `q` on number/customer,
date range), `GET /seller/orders/{order_id}` (customer info, shipping address, payment info, per-store timeline,
this store's items, summary), `POST /seller/orders/{order_id}/status {status, note?}`, `POST …/cancel {reason}`,
`GET /seller/orders/export.csv`. Invoice print/download: `GET /seller/orders/{id}/invoice` returns the invoice
*data*; PDF rendering is UI-side until a template is designed (Q28).

Summary block: design shows Subtotal / Shipping / Tax / Total. Shipping is charged once per buyer order; for a
multi-store order we show the store's subtotal and the order-level shipping with `shipping_shared: true` (Q3
already open). Tax = 0 until a tax rule exists.

## 4. Inventory

`GET /seller/inventory` — one row per **product** (as drawn) with `on_hand` (sum of variants), `reserved`
(units in this store's paid-but-unshipped orders — informational; stock is already decremented at checkout so
`available == on_hand`; Q29 asks what the designer meant by "Available"), `stock_status`, `updated_at`,
expandable `variants[]`. Tabs `all|low|out|recent` (recent = movement in last 7 days). KPIs: products,
variants, total units, out-of-stock count, low-stock count.
`POST /seller/inventory/{variant_id}/adjust {set | delta, reason?}` → `StockMovement`, stock status refresh.
`GET /seller/inventory/{variant_id}/movements`, `GET /seller/inventory/export.csv`.

## 5. Customers

Customer key = `user_id` or lower-cased `contact_email`. Per customer: name (contact first + last from the
latest order — full name, not the abbreviated "Olivia M." used on the buyer side; the seller ships to them, Q30),
email, `orders_count`, `total_spent_cents` (this store's lines, non-cancelled), `last_order` (number, date),
`first_order_at`, `type`: **new** = first order in the last 30 days, **returning** = ≥ 2 orders,
**one_time** = exactly 1 order older than 30 days; `status`: **active** = ordered in the last 90 days.
Overview: KPIs (total, new, returning, avg customer value = spent / customers, each with change %),
monthly new-vs-returning-vs-one-time series, breakdown, recent 5. List: tabs + `q` + sort. Detail:
profile + their store orders + notes. `POST /seller/customers/{key}/notes`. Export CSV.
"Message Customer" needs the undesigned Chat (Q20).

## 6. Dashboard

`GET /seller/dashboard?period=month` (also `week`, `quarter`, `year`; every KPI carries `previous` and
`change_pct`):
- `sales_cents` — this store's line totals on non-cancelled store orders **placed** in the period (booked)
- `revenue_cents` — same but **delivered** in the period (recognised). The design shows both "Total Sales" and
  "Total Revenue" with near-identical numbers; this is the meaningful distinction (Q31)
- `orders`, `products` (+ `new_this_month`), `low_stock`
- `revenue_series` — 12 months of the current year: revenue + orders
- `performance` — `product_views` (ProductEvent), `conversion_rate` = store orders / views,
  `aov_cents` = sales / orders, `customer_growth` = new customers
- `latest_orders` (5), `stock_breakdown` (in / low / out counts)
- "Traffic Channel" (visitors, socialising, active/inactive users) needs analytics data we do not collect;
  omitted (Q32). Header badge "2" = notifications, undesigned (Q20).

`GET /seller/search?q=` — header "Search anything": own products, order numbers, customers.

## 7. Login

Reuses `POST /auth/login`; the response already says `is_seller`. The portal additionally needs
`GET /seller/store` (exists) to decide onboarding vs dashboard. "Remember me" = client keeps the refresh
token (30 d) — no API change. Apple / Google sign-in are drawn but no provider is configured (Q33).

## 8. Order of work (each a commit, tests included)

1. Models + migrations: product `pending/rejected/tags/shipping/low_stock_threshold`, order `packed/returned`,
   `OrderStatusEvent.store`, `StockMovement`, `CustomerNote`, `Store.low_stock_threshold`; derived stock status;
   store-order helpers in `orders/services.py`.
2. Seller products (list, KPIs, wizard, images, variants, actions, analytics) + admin product review.
3. Seller inventory.
4. Seller orders (overview, list, detail, status, cancel, export).
5. Seller customers.
6. Dashboard + header search; `seed_demo` gains orders/customers for the demo store so screens are not empty.
7. Docs: developer guide v0.5, README, §10 questions; Figma re-check before each step.

Not in this sprint: Returns, Chat, Analytics page, Notifications, Team/Managers, social login, invoice PDF,
payments. Async: none of these endpoints await external services — all stay sync (see sprint-2 decision).

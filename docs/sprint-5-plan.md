# Sprint 5 — Premium sellers: advanced analytics + customer chat

> Status 2026-09-22: contract written before code (standing rule). Open questions Q42–Q48 in `docs/data-model.md`.
>
> **Design caveat.** The Figma changed on 2026-09-22 (Asma Choudhary) and the full-file API is rate-limited
> on the Starter plan until ~25 Sep, so the new frames could not be read. The snapshot in the repo has a
> **Chat** frame that is an empty shell (sidebar + header only), an **Analytics** sidebar entry with no
> screen, and a **"Message Customer"** row action on the Customers list. Everything below is therefore
> derived; when the limit lifts, re-snapshot, diff and reconcile (the backend contract rarely moves from a
> visual pass, but the *labels* will).

Two seller tiers. A **premium** store gets (1) an Advanced Analytics section and (2) a customer chat inbox.
Everything else is unchanged for both tiers.

## 1. Decisions (and what was rejected)

| Decision | Why | Rejected |
|---|---|---|
| **`Store.is_premium` boolean**, toggled by staff in Django admin. | User's call: no subscription machinery until the payments sprint. Django's admin `LogEntry` already records who flipped it and when. | A `StoreSubscription` table with dates/source (my original recommendation) — deferred, see Q42. |
| Entitlements live in **one data table** (`portal/entitlements.py`), keyed by a plan *name* derived from the flag (`plan_of(store) → "free" \| "premium"`). | Adding a feature or a third tier later is a table edit, and when subscriptions arrive only `plan_of()` changes. The boolean stays the only column. | Scattering `if store.is_premium` through the views. |
| **Buyers can message any active store; premium unlocks the seller's inbox.** | User's call. It is also the upsell hook: a free seller sees *"3 unread messages — upgrade to read and reply"*. | My recommendation (chat only exists on premium stores). Cost of the chosen path: a buyer can message a store that will never answer — mitigated below, and raised as Q44. |
| Free sellers may call **`GET /seller/chat/summary`** (counts only); every other chat route is `403 premium_required`. | Makes the upsell honest and gives the header badge something to show, without leaking message content. | Hiding chat entirely from free sellers (no upgrade signal). |
| Buyers must be **signed in**. | User's call; guests have no session to come back to. A guest who registers with the same e-mail keeps their orders. | Order-number + e-mail thread tokens (Track Order pattern) — Q45. |
| Chat is **polled REST**, not websockets. | The stack is sync WSGI by an earlier decision; polling `?after=` costs one indexed query. | Channels/ASGI + Redis — a platform change for a feature with no real-time requirement yet. |
| **One thread per (store, customer)**, with an optional `order` for context. | Matches the design's entry point ("Message Customer" on a customer row) and keeps the inbox a list of people, not tickets. | Per-order threads (Q46). |
| Messages that look like off-platform contact details are **flagged, not blocked**. | Standard marketplace practice; leakage is the main chat abuse. Staff get a queue; nobody's message is silently eaten. | Blocking (false positives break legitimate delivery chat) or ignoring it. |
| Advanced analytics is **derived from data we already collect** (`ProductEvent`, `OrderItem`, `OrderStatusEvent`, `StockMovement`, customers rollup). | No new tracking, no new writes on the request path. | A separate analytics store / rollup tables — premature at this volume (see engineering-practices §3). |

## 2. Plan and entitlements

- `Store.is_premium` (bool, default False) — the only new column on the seller side.
- `portal/entitlements.py`:
  - `FREE = "free"`, `PREMIUM = "premium"`; `plan_of(store)`; `features_for(store)`; `has(store, feature)`;
    `require(store, feature)` → `HttpError(403, "premium_required")`.
  - `FEATURES` table: `analytics_advanced`, `chat_inbox` (read + reply) — each with a label and a
    one-line description so the UI can render the upgrade card from the API, not from hard-coded copy.
- `GET /seller/plan` → `{plan, is_premium, features: [key…], catalogue: [{key, label, description, included}]}`.
- `GET /seller/dashboard` gains `"plan"` and `"locked_features"` so the sidebar can lock *Chat* and
  *Analytics* without a second call.
- Staff: `POST /admin/stores/{id}/plan {is_premium}` (StaffAuth) mirrors the admin toggle for tooling, and
  the Django admin gets `is_premium` in the Store list/filter/`list_editable`.

## 3. Chat (`chat` app)

### Model
| Model | Fields | Rules |
|---|---|---|
| `Conversation` | `store` FK, `customer` FK(User), `order` FK nullable (context), `subject`, `last_message_at`, `seller_unread`, `customer_unread`, `seller_last_read_at`, `customer_last_read_at`, `closed_at` | Unique `(store, customer)`. Created by the buyer's first message, or by a premium seller from a customer row. |
| `Message` | `conversation` FK, `sender_role` (`customer`\|`seller`\|`system`), `sender` FK(User) null for system, `body` (≤ 4000), `flagged_at`, `flag_reason` | Append-only. Counters and `last_message_at` are maintained in the one write path (`chat.services.send`). |

`chat/rules.py` holds the tables: `CONTACT_PATTERNS` (e-mail / phone / URL / "whatsapp"), `MAX_BODY`,
`RATE_LIMIT` (30 messages / 60 s per user, counted in SQL — LocMem is per-process, so a cache counter
would be wrong with 3 workers), `SYSTEM_GREETING`.

### Access
- Buyer (`JWTAuth`, mounted under `/me`): may open a thread with any **active store in the current market**
  (cross-market chat is meaningless — carts and orders are market-scoped; 404 otherwise).
- Seller (`StoreAuth` + `require(store, "chat_inbox")`): list, read, reply, close.
  **Exception:** `GET /seller/chat/summary` is allowed on any plan and returns
  `{unread, conversations, locked: true|false}`.
- Staff (`StaffAuth`): `GET /admin/chat/flagged` (flagged messages with context) — read-only.

### Endpoints
| Who | Route | Notes |
|---|---|---|
| buyer | `GET /me/conversations` | newest first, `unread`, `store`, `last_message` preview, `seller_can_reply` |
| buyer | `POST /me/conversations` `{store_id\|store_slug, order_id?, body}` | opens or reuses the thread, sends the first message (201) |
| buyer | `GET /me/conversations/{id}` · `GET …/messages?after=&limit=` | `after` = the id of the newest message the client holds (not a timestamp: responses round datetimes to milliseconds, so a timestamp cursor would re-deliver its own anchor) |
| buyer | `POST …/messages {body}` · `POST …/read` | |
| buyer | `GET /me/chat/summary` | header badge |
| seller | `GET /seller/chat` `?tab=all\|unread&q=` | paginated inbox; `403 premium_required` on free |
| seller | `GET /seller/chat/summary` | **allowed on free** — the upgrade hook |
| seller | `GET /seller/chat/{id}` · `GET …/messages` · `POST …/messages` · `POST …/read` · `POST …/close` | |
| seller | `POST /seller/customers/{key}/message {body}` | "Message Customer" on the Customers screen; 409 if that customer is a guest (no account) |
| staff | `GET /admin/chat/flagged` | |

`seller_can_reply` is in every buyer-facing payload so the UI can say *"this seller may not reply"*
instead of leaving the buyer guessing (the honest mitigation for §1's chosen scope).

## 4. Advanced analytics (`portal/analytics.py`, `/seller/analytics/*`)

All gated by `require(store, "analytics_advanced")`, all `?period=today|week|month|quarter|year`, all KPIs
shaped `{value, previous, change_pct}` like the existing dashboard. Nothing here duplicates the free
dashboard — it answers *why*, not *how much*.

| Route | Answers | Built from |
|---|---|---|
| `GET …/overview` | gross vs net sales (after cancels/returns), units, orders, AOV, repeat-customer rate, cancellation rate, return rate | `OrderItem` + status |
| `GET …/funnel` | views → wishlist → add-to-cart → purchase, with the conversion at each hop and the biggest drop-off | `ProductEvent` kinds |
| `GET …/products?sort=revenue\|units\|views\|conversion&limit=` | best sellers **and slow movers** (live, in stock, zero sales this period), each with views/units/revenue/conversion | `ProductEvent` + `OrderItem` |
| `GET …/search` | `store_queries`: what shoppers typed before viewing this store's products (search → view by the same actor within 30 min); `market_zero_result_queries`: searches in the store's market that returned nothing (clearly labelled as market-wide — a gap to stock) | `ProductEvent` SEARCH + VIEW |
| `GET …/customers` | new vs returning per month, repeat rate, average orders per customer, spend per customer, top 10 by spend | cached customers rollup |
| `GET …/fulfilment` | median/average hours from payment→shipped and shipped→delivered, per-status counts, cancellation/return rate | `Order.placed_at` + `OrderStatusEvent` (store-scoped; a per-store `pending` event is never written — that state is implicit at checkout) |
| `GET …/export.csv?report=` | any of the above as CSV | `portal.inventory.csv_response` |

The search report walks a capped window of events (`ANALYTICS_EVENT_SCAN_LIMIT`, default 5000, newest
first) and is cached 5 minutes per store+period — it is the only report that is not a pure aggregate.

## 5. Tests

`chat/tests.py` — open/reuse a thread, unread counters both ways, read receipts, polling `after=`,
free-seller inbox `403 premium_required` **and** summary allowed, guest `401`, cross-market `404`,
rate limit, contact-detail flagging, staff flagged queue, "Message Customer" to a guest customer `409`.
`portal/test_analytics.py` — one assertion per report against a fixture with known numbers, plus `403`
for every route on a free store. Query budgets: pin the inbox and the analytics overview.

## 5b. Design reconciliation — 2026-09-24 (from a local .fig export)

The REST API is metered by the plan the *file* lives on, and the design sits in the designer's Starter
space — so the original stayed locked. Two routes got us current anyway: a local **.fig export** decoded
offline (`docs/figma/kiwi.py`, `snapshot.py --from-fig`), and then a **duplicate of the file in the user's
own Drafts**, which the API serves normally. The snapshot in git is the full-fidelity API version taken from
that duplicate; `manifest.json` still records the *original's* version, so `--check` keeps watching the live
design rather than the frozen copy.

That sweep also exposed a bug of our own: `_wanted()` only accepted `FRAME`/`INSTANCE` top-level nodes, and
the designer draws dialogs as **groups** — so the five wizard popups below had been silently missing from
every snapshot we ever took.

**Node `779:6848`, which our snapshot called "Chat", is now "Analytics Overview".** The designer built the
Analytics section while we were building it blind, and added five product-wizard popups that belong to
sprint 3.

### Analytics is three screens, not one

| Screen | What it shows | Versus what we shipped |
|---|---|---|
| **Analytics Overview** — "Store Performance" | KPIs Total Sales R248,560 · Total Orders 1,284 · Average Order Value R193.43 · **Conversion Rate 3.84%**, all "vs previous period". Then **Sales Performance** (daily line chart, period selector), **Sales by Category** (donut with %), **Top Products** (rank, image, orders, revenue) and **Customer Snapshot** (New 328 / Returning 1,240 / One-Time 1,245). | `/analytics/overview` has the first three KPIs. **Conversion rate** exists only on the free dashboard → add it here. Missing: the **daily sales series**, **sales by category**, and the **three-way customer split** (which `portal.customers.Customer.type()` already computes). `/analytics/products` covers Top Products except the image. |
| **Sales Analytics** | KPIs **Gross R284,920 · Net R248,560 · Discounts R18,240 · Refunds R6,840**. **Revenue Over Time** (gross vs net, Daily/Weekly/Monthly). **Revenue Breakdown** waterfall: Gross 284,920 → −Discounts 18,240 → −Refunds 6,840 → −**Adjustments** 11,274 → Net 248,560. **Sales by Channel** donut: Website 67.8% / Mobile App 27.5% / Others 4.7%. **Sales by Category** table (category, orders, revenue, share). | Their *net* = gross − discounts − refunds − adjustments. Ours = gross − cancelled/returned lines: a different quantity with the same name. We have **no** discount attribution per store (that is Q34, now blocking), **no** refund amounts, **no** "adjustments" concept, and **no** sales channel on orders. |
| **Product Analytics** | KPIs only so far: Total Products 486 · Top Selling 24 · **Low Performing 38** · Avg. Product Revenue R510. Body not drawn. | `/analytics/products` already returns top + `slow_movers`; "low performing" is the same idea. Wait for the rest of the page before building more. |
| **Transactions** (new sidebar entry) | Title + Export only: "Track payments, payouts, fees and other financial activity for your store." | The payments sprint owns this. |

Also confirmed: **Customer Detail** has tabs Overview / Orders / Activity / **Messages** / Notes — the chat
model we shipped (one thread per store+customer, reachable from the customer record) matches the design.

### The wizard popups are a sprint-3 correction

| Popup | What it says | Impact |
|---|---|---|
| **Shipping** | "Delivery Options": Express Delivery 1–3 days **R25.00**, **Seller Delivery** 2–5 days R20.00, **Customer Pickup** "Pickup from store" R15.00. Plus **Processing Time** "1–2 business days" — which is our `Product.handling_days`, so part of sprint 3's guess was right. | Sprint 3 recorded the Shipping step as *empty* (Q24) and we stored weight/dimensions/handling days. The real design is **per-product delivery options with prices set by the seller**, including pickup. Today `DeliveryMethod` is platform-level per market and chosen by the buyer at checkout. This is a genuine model change — Q53. |
| **Media** | "PNG, JPG upto 1-MB" | We enforce `PRODUCT_IMAGE_MAX_BYTES = 5 MB`. Design says **1 MB** — Q54. |
| **Pricing** | Variant matrix: Color · Size · Price · Stock per row | Matches our options → variants model with per-variant price and stock. No change. |
| **Category Request** | Free text (0/250) + "Supporting Document (Optional)", PNG/JPG up to 1 MB | A seller can **request a new category**. We have no such endpoint — Q55. |
| **Review & Publish** | Product preview: category, stock, status, price, description, shipping, variants | Matches wizard step 5; it is a preview of data we already return. |

## 6. Not in this sprint

Subscriptions/billing and self-serve upgrade (payments sprint), websockets/push, chat attachments,
guest chat, per-order threads, premium badges on the storefront, e-mail notification of unread messages
(needs the background worker — perf step 6), Team/Managers sub-roles, Returns and Notifications screens.

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

## 5b. Design reconciliation — 2026-09-24 (partial, from renders)

`/files` and `/files/{key}/nodes` were both rate-limited, but **`/images` is metered separately**, so the
new screens were read as PNG renders instead (`snapshot.py --render`). The text snapshot in
`docs/figma/screens/` is therefore still the 2026-09-21 one — re-run `--index` + `--nodes` once the limit
clears (~29 Sep) and diff properly.

**Node `779:6848`, which our snapshot calls "Chat", is now "Analytics Overview".** The designer built the
Analytics section while we were building it blind. What the frames show:

| Frame | State | Versus what we shipped |
|---|---|---|
| **Analytics / Store Performance** | complete | KPI cards Total Sales · Total Orders · Average Order Value · **Conversion Rate** — we have the first three in `/analytics/overview`; conversion lives only on the free dashboard, so **move/duplicate it into the premium overview**. Then: **Sales Performance** daily line chart with a period selector, **Sales by Category** donut with %, **Top Products** table (rank, image, orders, revenue) and a **Customer Snapshot** (New / Returning / One-Time counts). We have none of the first two; `/analytics/products` covers Top Products except the image; `/analytics/customers` has the totals but not the three-way split that `portal.customers.Customer.type()` already computes. |
| **Analytics / Sales Analytics** | complete | KPIs **Gross Sales · Net Sales · Discounts · Refunds**. Their *net* = gross − discounts − refunds; our `net_sales_cents` = gross − cancelled/returned lines. Charts: **Revenue Over Time** (gross vs net, daily/weekly/monthly), **Revenue Breakdown** waterfall (gross → discounts → refunds → adjustments → net), **Sales by Category** table (orders, total, share), **Sales by Channel** donut (Website / Mobile App / Others). |
| **Analytics / Product Analytics** | KPI row only, body empty | Total Products · Top Selling · **Low Performing** · Avg. Product Revenue. Our `slow_movers` is the "low performing" idea; the rest of the page is not drawn yet. |
| **Transactions** (new sidebar entry) | title + Export button only | Payments, payouts and fees — the payments sprint. |
| **Customer Detail** | complete | Tabs Overview / Orders / Activity / **Messages** / Notes, and per-customer KPIs. Confirms the sprint-5 chat model: **one thread per (store, customer)**, reachable from the customer record. |

Blocking gaps the design creates (new §10 questions Q49–Q52): per-store **discount** attribution is
exactly Q34 and is now required, not optional; **refund amounts** do not exist until the PSP lands; we do
not record a **sales channel** on orders at all; and the sidebar now has **Transactions** alongside
Returns, Notifications and Team/Managers.

## 6. Not in this sprint

Subscriptions/billing and self-serve upgrade (payments sprint), websockets/push, chat attachments,
guest chat, per-order threads, premium badges on the storefront, e-mail notification of unread messages
(needs the background worker — perf step 6), Team/Managers sub-roles, Returns and Notifications screens.

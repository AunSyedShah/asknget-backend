# Sprint 7 — Sync with the 2026-10-09 design (team, notifications, transactions, product analytics)

> Written before code; revised 2026-10-12 with the owner's answers. **Design status (re-checked 2026-10-12):** the
> design changed on 2026-10-09 (snapshot `2408376029043747207`). **Nothing in this sprint's scope changed**: every
> existing screen is identical, and Team / Managers, Member Details, Edit Permissions, Notifications,
> Transactions (×3) differ from the 29 Sep versions only by a component rename (`Sidebar` → `Seller Sidebar`).
> What is new is an **Admin portal**, 13 screens, which is *not* in this sprint — see §9. Open questions are
> `docs/data-model.md` §10 Q62–Q83.

**Out of scope by decision (2026-09-29): the delivery-fee deduction (Q63).** Shipping stays the seller's income, as
decided in sprint 6. There is no "Delivery Fees" line charged to the seller anywhere in this sprint.

**Design principle (owner, 2026-10-12): the application should be as dynamic as possible.** Nothing a business
person might want to change is a constant in code. Commission, payout timing, thresholds, role presets,
invite lifetimes, seat limits and notification wording are all **data an admin edits in Django admin** — the
same mechanism sprint 4 used for countries and policies — and every value can be set per market and, where it
makes sense, per store. §1 is the one shared mechanism; sections 2–5 only declare what they need from it.

## 0. Where I depart from the design, and why

| Design shows | This plan does | Why |
|---|---|---|
| "Available to withdraw" and payouts | A real per-store **ledger**; payouts are **created and completed by staff**; no seller "Withdraw" | Money cannot move without a payment provider (Q9). A button that does nothing is worse than none. The ledger is the part that is hard to retrofit. |
| Seller "Update Status" on a transaction (Q65) | Staff-only | A seller must not mark their own payout paid. |
| Edit popup: 6 on/off toggles; details page: 8 sections × View / View + Manage (Q67) | One model: a level per section — `none`, `view`, `manage` — for all eight; roles are presets | The details page is the richer truth. A toggle is `manage ⇔ on`. |
| An activity log of what a member did (Q70) | Explicit, human-worded entries from the actions that matter | Logging every request would produce "PATCH /seller/products/…", which is exactly what the design avoids. |
| A commission that makes R1,240 → R1,120 (Q64) | **Commission rules an admin manages**, resolved per store, category, market or globally, with effective dates | Owner: "commission is dynamic". A rate that changes must not rewrite history, so each fee records the rate it used. |

## 1. The configuration layer (`config` app) — built first

Two kinds of dynamic value, because they have different shapes:

**Settings** — a typed registry for scalar tunables.
- `Setting` rows: `key`, `scope` (`global` | `country` | `store`), `country`, `store`, `value` (JSON), `note`,
  `updated_by`. Unique per `(key, scope, country, store)`.
- **Definitions live in code and are validated, not free text**: `Definition(key, type, default, label, help,
  scopes, min, max, choices)`. A row whose value does not satisfy its definition cannot be saved, in admin or
  anywhere else. The code supplies the default, so an empty table is a working system.
- Resolution, most specific first: the store's own value → its market's → global → the code default.
  `config.get("payouts.hold_days", store=store)`; `config.get_many([...], store=store)` is one query.
  No cache: an admin edit applies on the next request, as it does for countries.
- Admin: one list with the key's label, scope and effective value; a form that offers only registered keys
  and shows each one's default and help text.

**Rules and tables** — for things with history or structure.
- `CommissionRule`: `scope` (`global` | `country` | `category` | `store`) + the matching FK, `rate_bps`,
  `effective_from`, `is_active`, `note`. For a line in a store's order the winner is the most specific active rule
  already in effect (store → category, walking up to parent categories → country → global); ties go to the latest
  `effective_from`. A global rule of **500 (5 %)** is seeded as a starting point only — the owner sets the real ones.
- `TeamRole`: `key`, `name`, `permissions` JSON `{section: none|view|manage}`, `is_active`, `sort_order` — the role
  presets of §2 as data, so a new role needs no release.
- `NotificationTemplate`: see §4.

**Registered in this sprint** (every one editable, none hard-coded):

| Key | Type / default | Scopes | Used by |
|---|---|---|---|
| `payouts.hold_mode` | `return_window` \| `fixed_days` \| `none` / `return_window` | global, country, store | when a sale becomes available |
| `payouts.hold_days` | int ≥ 0 / 7 (used when mode is `fixed_days`) | global, country, store | same |
| `payouts.minimum_cents` | int ≥ 0 / 0 | global, country | the smallest payout staff can create |
| `analytics.low_performer_min_views` | int ≥ 1 / 20 | global, country, store | Product Analytics |
| `analytics.low_performer_limit` | int 1–50 / 5 | global, country | the length of that list |
| `team.invite_expiry_days` | int 1–60 / 7 | global, country | invite links |
| `team.max_members` | int ≥ 0 / 0 (= unlimited) | global, country, store | seat limit — a premium lever later without code |
| `notifications.recent_days` | int 1–365 / 90 | global | how far back the list reaches |

**Follow-up candidates** (existing constants that should move here, not touched in this sprint): the product image
size limit, chat rate limit, OTP cooldown, analytics search-scan limit, customer-cache TTL.

## 2. Team / Managers (`team` app)

Why next: every later router must be permission-aware, and "who sees which notification" depends on it.

**Models**
- `StoreMember`: `store`, `user` (null until the invite is accepted), `email`, `role` (→ `TeamRole`),
  `permissions` JSON `{section: none|view|manage}` (starts as the role's), `status` (`invited` | `active` |
  `suspended`), `invited_by`, `invite_token_hash` (sha256 — the raw token exists only in the e-mail),
  `invite_expires_at`, `joined_at`, `last_active_at`. Unique `(store, email)`.
- The **owner is implicit** (`Store.owner`): always every permission, never a row, cannot be removed. The list
  endpoint synthesises the Owner row so the table matches the design.
- `StoreActivity`: `store`, `actor`, `action` (slug), `summary` ("Updated price for Wireless Earbuds Pro"),
  `target_type` / `target_id`, `created_at`. Append-only.

**Sections** — the design's eight: `dashboard, orders, products, inventory, customers, marketing, analytics,
transactions`. Mapping of what already exists: chat inbox → `customers`; delivery options → `products`; header
search → `dashboard`. **Owner only:** team management and the plan page. `marketing` exists in the model but
guards nothing yet (Q69).

**Roles** — seeded as `TeamRole` rows: Manager, Inventory Manager, Order Manager, Marketing Manager, Finance
Manager, Support Agent. Choosing a role copies its levels; the owner may override any section (the popup), and
the member then reads `custom`. Editing a `TeamRole` later changes only members assigned afterwards.

**Enforcement** — `StoreAuth(section="orders")` on each router. `GET`/`HEAD` need `view`, everything else
`manage`; otherwise `403 permission_denied` naming the section. Premium gating stays a separate, store-level check.
The store is the one the user **owns**, else the one they are an **active member** of; `X-Store: <id>` chooses when
there are several. `request.member` is `None` for the owner. `last_active_at` is touched at most once a minute.
`StoreAuth` stops inheriting `SellerAuth`: the owner path still requires `is_seller`, a member does not (they never
filled in an application). A removed or suspended member loses access on the next request.

**Endpoints** — `/seller/team` (owner only): list with the four KPI cards and filters (status, role, last active,
`q`); `POST /invites {email, role, permissions?}` (refused with `409 member_limit` past `team.max_members`);
`POST /{id}/resend`; `GET|PATCH|DELETE /{id}`; `GET /{id}/activity`; `GET /roles` (the active `TeamRole` rows, so
the UI never hard-codes them). `POST /auth/team/accept {token, full_name?, password?}` — an existing account must
be signed in as the invited e-mail; otherwise one is created (e-mail marked verified, the link arrived there).
`GET /seller/me` → store, `is_owner`, effective permissions, so the sidebar hides what you cannot open. Invite
links use a `PORTAL_URL` setting (falls back to `STOREFRONT_URL`; the owner supplies the address).

**Activity logged at first** (from the service layer): product price / stock / status changes, order status changes,
CSV exports, delivery-option edits, team changes.

## 3. Transactions (`ledger` app)

**Models**
- `LedgerEntry` (append-only): `store`, `type` (`sale` | `fee` | `refund` | `adjustment` | `payout`), signed
  `amount_cents`, `currency`, `status` (`pending` | `processing` | `completed` | `failed`), `order`, `payout`,
  `reference` (`TXN-YYYYMMDD-<number>` from a locked counter row), `description`, `available_at`,
  `meta` JSON (the commission **rate and rule id** a fee used; the hold policy a sale used), `created_by`.
  Unique `(order, store, type)` for `sale`, `fee` and `refund` — the idempotency guarantee.
- `Payout`: `store`, `amount_cents`, `status` (`processing` | `completed` | `failed`), `reference`, `created_by`,
  `completed_at`, `note`.

**Posting rules**
- **Payment confirmed** (the one event is `orders.services.on_payment_confirmed`, called where `set_status` moves
  an unpaid order to paid, and later by the PSP webhook) → per store: `sale` = its line totals − its voucher share
  (`store_discount_cents`) + its `OrderDelivery.price_cents`; `fee` = −Σ over its lines of
  `rate(line) × line_total`, scaled by (net ÷ gross) so the voucher share reduces the fee proportionally, rounded
  once per store. `rate(line)` is the `CommissionRule` winner at that moment, recorded in `meta`.
- **`available_at`** is set from the store's resolved `payouts.hold_mode`: `return_window` → placed + the order's
  return window; `fixed_days` → + `payouts.hold_days`; `none` → now. The admin decides, per market or per store;
  changing it affects only sales posted afterwards.
- **A store cancels or returns its lines, or staff cancel/refund the order** → `refund` = −what that store's
  `sale` posted, and the `fee` is reversed (+). Once per store per order.
- **Staff adjustment** → `adjustment`, signed (Q66), note required.
- **Payout** → staff create it for ≥ `payouts.minimum_cents` and ≤ the available balance → a `payout` entry
  (−amount, `processing`); completing it marks both `completed` and notifies the seller; failing it reverses it.

**Balances are computed, never stored.** Available = Σ entries with `available_at ≤ now` (payouts count at once);
Pending payout = Σ processing payouts; Total sales = Σ `sale` in the period; Total fees = −Σ `fee`. A sale/fee row
reads `completed` once its `available_at` has passed.

**Endpoints** — `/seller/transactions/overview?period=` (the four cards, recent 5, the summary: Sales Revenue,
Platform Fees, Refunds, Adjustments, Net Payout); `/seller/transactions?tab=&q=&status=&date_from=&date_to=&amount_min=&amount_max=`
with tab counts; `/{reference}` (order, customer, payment method, amount breakdown); `/export.csv`. Staff:
`POST /admin/ledger/adjustments`, `/admin/payouts` (create, list, `PATCH /{id}/status`). Section: `transactions`.

`manage.py backfill_ledger` posts entries for paid orders that already exist (idempotent), using the rules in
force today. Sales Analytics' refunds and adjustments stop being a constant 0 and read from the ledger (Q50).

## 4. Notifications (`notifications` app)

- `StoreNotification`: `store`, `category` (`orders` | `payments` | `inventory` | `customers` | `system`), `kind`,
  `params` JSON (`{"order": "ORD-28491", "customer": "Olivia Martin"}`), `link_type` / `link_id`, `created_at`. **The
  text is not stored.**
- `NotificationTemplate`: `kind` (unique), `category`, `title` and `body` as per-language JSON (the sprint 4
  `{"en": "...", "ur": "..."}` convention, falling back through the market's languages), `is_active`. Seeded in
  English from code; an admin edits the wording or adds a language without a release. Rendered when read, in the
  reader's language, so one notification reads correctly for an English owner and an Urdu manager.
- `NotificationRead`: unique `(notification, user)` — read state is **per person**, now that several people share
  a store.
- A member sees a category only if they can `view` its section (orders → orders, payments → transactions,
  inventory → inventory, customers → customers); `system` is for everyone.

| Event | Emitted from |
|---|---|
| New order received + Payment received | `on_payment_confirmed` (the same hook as the ledger), once per store in the order |
| Order cancelled | buyer cancels, or staff cancel |
| Low stock / Out of stock | `refresh_stock_status` **when the status changes** into low/out — once per crossing, not per sale |
| New customer | first paid order from that customer at this store (same key as `portal.customers`) |
| Payout processed | staff complete a payout |
| System | product approved / rejected, plan changed |

Emitted inside the same transaction as the event, so a rollback takes the notification with it. Endpoints:
`GET /seller/notifications?tab=&page=` (the client groups Today / Yesterday by `created_at`), `GET /summary`
(unread per tab and in total, for the header badge), `POST /{id}/read`, `POST /read-all`. In-app only (e-mail and
push need the worker, Q48).

## 5. Product Analytics

`/seller/analytics/products` gains `daily: [{date, orders, units, revenue_cents, views}]` for the Product Performance
chart (its four tabs) and `low_performers`: the lowest conversion among products with at least
`analytics.low_performer_min_views` views in the period, at most `analytics.low_performer_limit` of them (Q72).
Both are admin-set. `slow_movers` stays.

## 6. Order of work, tests, docs

| Step | Delivers | Commit |
|---|---|---|
| 0 | Re-check Figma; fold in drift — **done 2026-10-12** | — |
| 1 | `config` app: settings registry, `CommissionRule`, admin, resolution, tests | `Sprint 7.1: configuration layer` |
| 2 | `team` app + `TeamRole` + `StoreAuth(section=)` on every seller router + `/seller/me` + invites | `Sprint 7.2: team and permissions` |
| 3 | `ledger` app, `on_payment_confirmed`, backfill, staff endpoints, analytics wired to the ledger | `Sprint 7.3: transactions` |
| 4 | `notifications` app, templates and emitters | `Sprint 7.4: notifications` |
| 5 | Product analytics series and low performers | `Sprint 7.5: product analytics` |
| 6 | Docs, developer guide v0.9, `seed_demo`, frontend handoff updated | `Sprint 7 docs` |

**Tests that matter:** a setting resolves store → market → global → default, and an invalid value is refused; the
commission winner for store / category (including a parent category) / market / global, and a future-dated rule
changes nothing yet; every router × `none`/`view`/`manage` (a matrix, not spot checks); an owner's query count is
unchanged by the new auth (the budget tests in `core/test_query_budgets.py` stay as they are); ledger idempotency
(confirming twice posts once; cancelling twice refunds once); fees and sales sum across stores to the order; a
rate change after posting leaves old fees untouched; hold modes each set `available_at` correctly; invite
lifecycle (expiry, reuse, wrong account, seat limit); a suspended member's next request fails; low-stock fires once
per crossing; read state is per user; a notification renders in two languages.

## 7. Decisions

**Settled by the owner (2026-10-12):** commission is dynamic (§1); the admin decides payout timing (§3); the
low-performer threshold is set by the admin (§5); the application is dynamic wherever possible (§1).

**Taken as defaults, reversible, tell me if any is wrong:**
1. Team management is **owner-only**; seats are free and unlimited until `team.max_members` is set.
2. `PORTAL_URL` for invite links is a setting; until the owner supplies the seller portal's address, invite e-mails
   point at `STOREFRONT_URL`.
3. **Marketing** is a permission with nothing behind it yet (Q69).
4. **Notifications are in-app only** for now (Q48).
5. **Adjustments are staff-created and signed** (Q66).
6. **Transaction reference** is `TXN-YYYYMMDD-<number>` everywhere: the seller screens show `TXN-10482`, the admin
   Order Details shows `TXN-20260930-28501`; the dated form sorts and reads on a statement (Q79).

## 8. Not in this sprint

The delivery-fee deduction (Q63), seller withdrawals and any PSP payout, tax lines, the Returns flow (Q73), e-mail /
push notifications, a store switcher in the UI (the API supports it through `X-Store`), invoices, an API or UI
for settings (Django admin covers them), and the Admin portal below.

## 9. New in the 9 Oct design: the Admin portal (proposed Sprint 8)

Thirteen staff-facing screens: Admin Dashboard, Sellers, Seller Details, Category Management, Add Category (popup),
Subcategories, Category Details, Orders, Order Details, Cancel Order, Update Order Status (popup), Products,
Product Detail. Today every staff endpoint hangs off `is_staff` and there is no admin role model (Q36), which is
why this is a sprint of its own and not an add-on to a seller-facing one.

| Screen | Already there | Missing |
|---|---|---|
| **Admin Dashboard** | — | GMV, orders, active sellers, customers, each against the previous 30 days; a GMV and orders series (weekly / 1 month / 6 months / 1 year); the order-status donut; recent orders. *Visitors and active users need web analytics we do not collect (Q32).* |
| **Sellers**, **Seller Details** | KYC review queue (`/admin/seller-applications`), `Store.status` including `suspended`, the plan toggle | A store directory with product / order / revenue counts and the tabs Active / Pending / Suspended / Inactive; a suspend and reactivate action; a seller rating; the detail tabs Business, Products, Orders, Performance, Payouts (the sprint 7 ledger), Activity; **"+ Add New Seller"** (Q75). |
| **Category Management**, **Add Category**, **Subcategories**, **Category Details** | `Category` already has `parent`, `image`, `sort_order`, `is_active` | Admin create / edit / reorder / hide; per-category counts (subcategories, products, last updated); a hide that cascades to the storefront; the product list per category. |
| **Orders**, **Order Details**, **Cancel Order**, **Update Order Status** | `/admin/orders` list, detail, status change | Filters (status, date range, seller), tab counts, export, a cancel-with-reason action, the seller and payment blocks. |
| **Products**, **Product Detail** | `/admin/products` pending queue with approve / reject | An all-status list with counts, a **suspended** product state (we have none, Q77), a suspend action, detail tabs Variants / Inventory / History / Reviews, export. |

**Recommendation:** finish this sprint first. The admin portal is mostly read-side aggregation over data that exists,
so it is low-risk once Q74–Q83 are answered, and it reuses what this sprint builds (the ledger for Payouts,
`StoreActivity` for Activity, the settings layer for anything an admin tunes). Roughly the size of sprint 6.

## 10. As built (2026-10-12) — where it differs from the plan above

Steps 1–6 are done and tested (423 tests, SQLite and Postgres). Differences, so the plan is not mistaken for the record:

| Plan said | Built | Why |
|---|---|---|
| A sale becomes available at *placed + the return window* | It stays **held until the store's goods are delivered**, then `payouts.hold_mode` decides (`return_window` / `fixed_days` / `none`); `none` is the only mode that releases at payment | Money must not be releasable before the goods arrive (Q84) |
| A failed payout "reverses" its entry | A failed payout's entry is **excluded from every balance** | Same effect, no extra row to keep consistent |
| Refund reverses the fee | Separate `fee_refund` entry type | The unique-per-posting rule would otherwise forbid a second `fee` entry |
| `Country.commission_rate_bps` column | `CommissionRule` table: store / category / market / global, effective-dated | "Commission is dynamic" needs history and per-category / per-store deals |
| "Plan page owner-only" | `GET /seller/plan` is readable by **any member** | The sidebar needs it to draw the locks |
| Sales Analytics refunds "read from the ledger" | Refunds computed from the order lines (cancelled / returned, net of their voucher share); only **adjustments** come from the ledger | Gross had to change to include later-refunded lines, otherwise they'd be subtracted twice; it also keeps the figure correct for orders that predate the ledger (Q85) |
| — | `manage.py show_settings`, `backfill_ledger`, `poe endpoints` | Operating tools the dynamic design needs |
| — | Backfilled history is dated when it happened and keeps refunds | Found while seeding: a naive backfill put a year of history in the current month (Q88) |

Not done, by design: the delivery-fee deduction (Q63); seller withdrawals; e-mail / push notifications; a store switcher UI; the Admin portal (§9).

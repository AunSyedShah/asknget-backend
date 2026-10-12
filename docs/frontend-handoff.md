# Frontend handoff — what the backend guarantees, and how to read the design

> For whoever works in the **frontend repository** (a Claude session or an engineer). Written 2026-10-12 from the
> code, not from memory: the endpoint index at the end is generated from the OpenAPI schema. If this file and the
> running API disagree, the API wins; `GET /api/v1/openapi.json` is the contract and `/api/v1/docs` is its UI.
> Backend repo: `github.com/AunSyedShah/asknget-backend` (renamed from `asknget`; the old URL redirects).

## 1. The task, and what is still undecided

Make the frontend **responsive** and faithful to the Figma design, with every screen wired to the real API.

**The Figma file only has desktop frames** (buyer storefront and seller portal alike). There is no mobile or
tablet design, so responsiveness cannot be copied from it; it has to be decided. Until the owner says otherwise,
use these defaults and state them in the PR:

- Breakpoints: **360 / 768 / 1024 / 1280 px**, mobile-first.
- Navigation: collapses to a drawer below 1024 px. Product and category grids: 4 → 3 → 2 → 1 columns.
- Wide tables (seller products, orders, customers, inventory): cards below 768 px, never a sideways-scrolling page.
- Popups and the product wizard become full-screen sheets on mobile.

Open questions for the owner (ask, do not guess): buyer storefront first or seller portal too and in what order;
whether any page still uses mock data; the definition of done (which widths, screenshots or Playwright).

## 2. Environments

| Where | Base URL | Notes |
|---|---|---|
| Django dev server | `http://localhost:8000/api/v1` | `uv run poe dev` in `backend/` (SQLite). Docs at `/api/v1/docs`. |
| Docker (nginx + Postgres) | `http://localhost:8080/api/v1` | `docker compose up -d`; the port is `HTTP_PORT`. |
| Vercel | the project's production URL | `asknget-backend.vercel.app` was the host in the deployment branch; confirm it. Needs a hosted Postgres; see `docs/environment.md` section 6. |

Demo accounts (seeded by `uv run poe seed`): `customer@asknget.demo`, `seller@asknget.demo`, `admin@asknget.demo`;
the password is in `backend/README.md`. The seller has a **premium** store (Chat and Advanced Analytics work).

## 3. The contract — read this before writing any fetch code

**Paths have no trailing slash**: `/cart`, `/cart/items`. `/cart/` is a 404. Join `BASE_URL` and the path with exactly
one slash; a base URL ending in `/` plus a path starting with `/` produces `//api/...`. The backend tolerates
that now, but fix it at the source.

**Region and language are codes, not names.** `country: "PK"`, `language: "en"`, `currency: "PKR"`, timezone
`Asia/Karachi`. Never render them. Country names come from `GET /regions` (`name`) or
`Intl.DisplayNames(lang, {type: 'region'})`; language names from `Intl.DisplayNames(lang, {type: 'language'})`
(the API returns language codes only). Send the same codes back.

**Request headers** (all optional; send them on every call):

| Header | Meaning |
|---|---|
| `Authorization: Bearer <access_token>` | The signed-in user. |
| `X-Region: PK` | Market override. Unknown or inactive → `400 unknown_region`. Without it the market comes from the host's subdomain (`za.…`, `pk.…`), then the default country. |
| `X-Language: ur` | The user's saved language (`GET /me` → `language`). Otherwise `Accept-Language`, then the country's default. |
| `X-Timezone: Asia/Karachi` | Otherwise the country's timezone. |
| `X-Cart-Token` | **Guest** carts. The first `POST /cart/items` returns `cart_token` in the body; send it back on every cart and checkout call. Signing in merges the guest cart into the account cart and retires the token. |

Responses carry `Content-Language` and `X-Region`. CORS exposes only those and `content-disposition`, and allows only
the request headers above plus `content-type`. **Do not use `credentials: 'include'`**: auth is the Bearer header
and CORS credentials are off. The frontend's origin must be listed in the backend's `CORS_ALLOWED_ORIGINS`.

**Auth.** `POST /auth/login {email, password}` → `{user, tokens: {access_token, refresh_token, expires_in}}` (the
access token lives 15 minutes). On a `401`, call `POST /auth/refresh {refresh_token}` once and retry; if that
fails, send the user to login. `user.locale` is `{country, language, timezone, currency}`.

**Errors.** Every error is `{"detail": "<one message>"}`. Validation errors (`422`) add
`"errors": [{"field": "address.city", "message": "…"}]` so a form can highlight the field. Meanings worth handling:

| Status / detail | Meaning |
|---|---|
| `403 no_active_store` | Seller whose application is not approved and activated yet → route to application status. |
| `403 premium_required` | The store's plan does not include this area. Read `GET /seller/plan` to decide what to lock; never hard-code feature names. |
| `403 Seller account required` | A buyer account called a seller endpoint. |
| `409 cart_region_mismatch` | Item from another market. Offer "clear cart and switch" = `DELETE /cart/items`, then add again. |
| `409 ships_outside_region` | Shipping address country differs from the cart's market. |

**Lists** return `{items, total, page, per_page, pages}`; query with `?page=&per_page=` (default 12, max 100).

**Money** is an integer in minor units named `*_cents`, with a `currency` code. Format with
`new Intl.NumberFormat(lang, {style: 'currency', currency}).format(cents / 100)`. Percentages are plain numbers
(`share_pct: 23.53`). Timestamps are ISO 8601 in UTC; show them in the user's timezone.

**One market per cart and per seller.** A store sells in exactly one country and currency.

## 4. Things that changed recently and are easy to get wrong

- **Shipping is chosen per seller.** `GET /cart` returns `deliveries[]`, one entry per store:
  `{store_id, store_name, options[{id, name, eta_label, kind, price_cents}], selected_option_id, price_cents}`.
  Show one shipping block per seller. Change a choice with `PATCH /cart/delivery {store_id, option_id}`.
  `summary.shipping_cents` is the sum. `kind: "pickup"` is never selected by default. There is no
  `delivery_method` and no `GET /delivery-methods` in use any more.
- **Checkout** takes `deliveries` (optional; the cart's selections are used if omitted) and `channel`
  (`web` | `mobile_app` | `other`; send what the client really is). Order detail returns
  `delivery_method.per_store[]`.
- **Payments are not integrated yet.** `POST /checkout` creates the order as `pending_payment`/`unpaid`. Build the
  card form, but do not expect tokenisation or a payment confirmation from the API.
- **Seller analytics `period`** is one of `today | week | month | quarter | year`; other values are `422`. These are
  *calendar* periods ("month" = this month so far). The sales report also takes `bucket=daily|weekly|monthly`.
- **Product images**: PNG or JPG, at most 1 MB.
- **Chat** is polled: `GET …/messages?after=<last message id>`. Use the id, never a timestamp.
- **Premium gating** applies to `/seller/analytics/*` and the chat inbox (`/seller/chat*`). The one exception is `GET /seller/chat/summary` (unread counts), which every plan may call, so a standard seller can see that messages are waiting.

## 5. In the design but not in the backend yet

Do not wire these to the API (render them from static or empty states, or leave them out, as the owner decides):
**Team / Managers, Member Details, Edit Permissions, Notifications, Transactions (overview, list, details), Returns,
Category Request**, the whole **Admin portal** (13 screens added 2026-10-09: dashboard, sellers, categories, orders,
products; planned as the sprint after next), refund and adjustment amounts in Sales Analytics, the daily Product Performance chart and the
Low Performing list in Product Analytics. They are planned for the next backend sprint
(`docs/data-model.md`, questions Q62–Q73). Report what you need instead of assuming a shape.

## 6. Reading the Figma design

Two ways, both from the backend repo (run from its root, Python 3.12+):

1. **Text outlines** (fast, no network): `docs/figma/screens/<Screen name>__<node id>.txt`. One file per screen:
   `[Name]` is a frame or group, `[I:Component | Variant]` a component instance, `T:` a text layer, indentation is
   nesting. They give structure, copy and component states, not colours, spacing or images. Files named
   `…__new.txt` are screens added after the last full sync; their node id is not known yet.
   `docs/figma/manifest.json` lists them all.
2. **Images** (needed for layout and spacing): `python docs/figma/snapshot.py --file lWcZqbOFLdHJ2dERb5hZuB --render "<frame name or node id>" --scale 1`
   saves a PNG into `docs/figma/renders/` (git-ignored). Use `--scale 0.2` for an overview. Use **that file**, the
   owner's duplicate: the original sits on someone else's Starter plan and is rate-limited for days at a time.

`FIGMA_TOKEN` is the owner's personal token. The tool reads it from the backend repo's root `.env` (git-ignored), then
from the environment, so a new session finds it without being told. **Never print it, paste it into a file, or commit it.** If a call returns `429`, stop; do not retry
in a loop. Run `python docs/figma/snapshot.py --check` to learn whether the design changed since the last snapshot.
Treat the design as a hypothesis: where it contradicts the API or itself, log it instead of silently choosing.

## 7. Suggested way of working

1. **Audit, read-only.** List every API call the frontend makes and compare it with the index below; list every
   page and the Figma frame it should match. Produce the mismatches before changing anything.
2. **Agree the breakpoint plan** (section 1) with the owner.
3. **Fix page group by page group**, wiring real API calls as you go. Verify each at 360, 768, 1024 and 1280 px in a
   real browser, and compare against the rendered frame.
4. **Do not change the backend from the frontend session.** Write what you need (a missing field, a wrong shape)
   into a `BACKEND_ASKS.md` in the frontend repo, one item per entry with the request, the response you got, and what
   you expected. The owner brings it to the backend session.

## 8. Endpoint index (generated from the OpenAPI schema)

165 operations in 26 groups.

#### auth

| Method | Path | Auth | What it does |
|---|---|---|---|
| POST | `/api/v1/api/v1/auth/login` | - | Login |
| POST | `/api/v1/api/v1/auth/logout` | bearer | Logout |
| POST | `/api/v1/api/v1/auth/otp/send` | bearer | Send a 6-digit code by email or SMS |
| POST | `/api/v1/api/v1/auth/otp/verify` | - | Otp Verify |
| POST | `/api/v1/api/v1/auth/password/forgot` | - | Password Forgot |
| POST | `/api/v1/api/v1/auth/password/reset` | - | Password Reset |
| POST | `/api/v1/api/v1/auth/refresh` | - | Rotate refresh token |
| POST | `/api/v1/api/v1/auth/register` | - | Register as a customer |
| POST | `/api/v1/api/v1/auth/seller/register` | - | Register as a seller ('Create your seller account') |

#### cart

| Method | Path | Auth | What it does |
|---|---|---|---|
| GET | `/api/v1/api/v1/cart` | bearer | Current cart (creates a guest cart if none) |
| PATCH | `/api/v1/api/v1/cart` | bearer | Order note / delivery type |
| PATCH | `/api/v1/api/v1/cart/delivery` | bearer | Choose a seller's delivery option for their part of the cart |
| DELETE | `/api/v1/api/v1/cart/items` | bearer | Empty the cart ('clear cart and switch region') |
| POST | `/api/v1/api/v1/cart/items` | bearer | 'Add to Cart' (merges qty if the variant is already there) |
| DELETE | `/api/v1/api/v1/cart/items/{item_id}` | bearer | Remove line |
| PATCH | `/api/v1/api/v1/cart/items/{item_id}` | bearer | Change quantity |
| DELETE | `/api/v1/api/v1/cart/voucher` | bearer | Cart Remove Voucher |
| POST | `/api/v1/api/v1/cart/voucher` | bearer | 'Discount Voucher → Apply' |

#### catalog

| Method | Path | Auth | What it does |
|---|---|---|---|
| GET | `/api/v1/api/v1/brands` | - | Brands |
| GET | `/api/v1/api/v1/categories` | - | Category tree with item counts |
| GET | `/api/v1/api/v1/products` | bearer | Search / browse products |
| GET | `/api/v1/api/v1/products/best-sellers` | bearer | Landing: 'Best Sellers' |
| GET | `/api/v1/api/v1/products/featured` | bearer | Landing: 'Featured Products' |
| GET | `/api/v1/api/v1/products/filters` | - | Sidebar filter options (categories, brands, colours, price range) |
| GET | `/api/v1/api/v1/products/new-arrivals` | bearer | Product New Arrivals |
| GET | `/api/v1/api/v1/products/top-discounted` | bearer | Landing: 'Top Discounted' |
| GET | `/api/v1/api/v1/products/{slug}` | bearer | Product Details |
| GET | `/api/v1/api/v1/products/{slug}/related` | bearer | 'You may also like' |
| GET | `/api/v1/api/v1/products/{slug}/reviews` | - | Reviews tab list |
| POST | `/api/v1/api/v1/products/{slug}/reviews` | bearer | 'Add your review' (multipart; guests must send name + email) |
| GET | `/api/v1/api/v1/products/{slug}/reviews/summary` | - | '4.9 out of 5 (205 Reviews)' + star bars |

#### checkout

| Method | Path | Auth | What it does |
|---|---|---|---|
| POST | `/api/v1/api/v1/checkout` | bearer | 'Place Order' (no payment capture yet — order stays pending_payment) |

#### discovery

| Method | Path | Auth | What it does |
|---|---|---|---|
| GET | `/api/v1/api/v1/products/{slug}/frequently-bought-together` | bearer | Frequently Bought Together |
| GET | `/api/v1/api/v1/products/{slug}/similar` | bearer | 'You may also like' (semantic) |
| GET | `/api/v1/api/v1/recommendations` | bearer | 'Recommended for you' |
| GET | `/api/v1/api/v1/search/suggest` | - | Search-bar suggestions |

#### lookups

| Method | Path | Auth | What it does |
|---|---|---|---|
| GET | `/api/v1/api/v1/lookups/banks` | - | 'Select your bank' |
| GET | `/api/v1/api/v1/lookups/seller` | - | Dropdown options for the seller wizard (+ the market's KYC policy) |

#### marketing

| Method | Path | Auth | What it does |
|---|---|---|---|
| GET | `/api/v1/api/v1/banners` | - | Landing hero / promo tiles (live ones only) |
| POST | `/api/v1/api/v1/contact` | bearer | 'Send a Message' |
| POST | `/api/v1/api/v1/newsletter/subscribe` | - | 'Subscribe Now' |
| GET | `/api/v1/api/v1/site-info` | - | Footer + 'Contact Info' block |

#### me

| Method | Path | Auth | What it does |
|---|---|---|---|
| GET | `/api/v1/api/v1/me` | bearer | Me |
| PATCH | `/api/v1/api/v1/me` | bearer | Me Update |
| GET | `/api/v1/api/v1/me/addresses` | bearer | Address List |
| POST | `/api/v1/api/v1/me/addresses` | bearer | Address Create |
| DELETE | `/api/v1/api/v1/me/addresses/{address_id}` | bearer | Address Delete |
| GET | `/api/v1/api/v1/me/addresses/{address_id}` | bearer | Address Detail |
| PATCH | `/api/v1/api/v1/me/addresses/{address_id}` | bearer | Address Update |
| POST | `/api/v1/api/v1/me/addresses/{address_id}/default` | bearer | Address Set Default |
| POST | `/api/v1/api/v1/me/avatar` | bearer | Upload profile photo (PNG/JPG/GIF, ≤ 2 MB) |
| GET | `/api/v1/api/v1/me/chat/summary` | bearer | Unread badge |
| GET | `/api/v1/api/v1/me/conversations` | bearer | My conversations with sellers |
| POST | `/api/v1/api/v1/me/conversations` | bearer | 'Message seller' — opens the thread (or reuses it) and sends the first message |
| GET | `/api/v1/api/v1/me/conversations/{conversation_id}` | bearer | My Conversation |
| GET | `/api/v1/api/v1/me/conversations/{conversation_id}/messages` | bearer | Poll for new messages |
| POST | `/api/v1/api/v1/me/conversations/{conversation_id}/messages` | bearer | My Send |
| POST | `/api/v1/api/v1/me/conversations/{conversation_id}/read` | bearer | My Read |
| GET | `/api/v1/api/v1/me/orders` | bearer | My Orders (All / Ongoing / Completed / Cancelled) |
| GET | `/api/v1/api/v1/me/orders/{order_id}` | bearer | 'View Detail' / 'Track Order' |
| POST | `/api/v1/api/v1/me/orders/{order_id}/cancel` | bearer | My Order Cancel |
| POST | `/api/v1/api/v1/me/orders/{order_id}/reorder` | bearer | 'Re-Order' → items back in the cart |
| POST | `/api/v1/api/v1/me/password` | bearer | Change password (Password Manager) |
| GET | `/api/v1/api/v1/me/payment-methods` | bearer | Payment Method List |
| POST | `/api/v1/api/v1/me/payment-methods` | bearer | Payment Method Create |
| DELETE | `/api/v1/api/v1/me/payment-methods/{pm_id}` | bearer | Payment Method Delete |
| POST | `/api/v1/api/v1/me/payment-methods/{pm_id}/default` | bearer | Payment Method Set Default |
| POST | `/api/v1/api/v1/me/terms` | bearer | Me Accept Terms |
| GET | `/api/v1/api/v1/me/wishlist` | bearer | My Account – Wishlist |
| DELETE | `/api/v1/api/v1/me/wishlist/{product_id}` | bearer | Wishlist Remove |
| PUT | `/api/v1/api/v1/me/wishlist/{product_id}` | bearer | Add to wishlist (idempotent) |

#### meta

| Method | Path | Auth | What it does |
|---|---|---|---|
| GET | `/api/v1/api/v1/health` | - | Liveness probe |

#### orders

| Method | Path | Auth | What it does |
|---|---|---|---|
| POST | `/api/v1/api/v1/orders/track` | - | Track Order by ID + email |

#### regions

| Method | Path | Auth | What it does |
|---|---|---|---|
| GET | `/api/v1/api/v1/regions` | - | Active countries (country switcher) |
| GET | `/api/v1/api/v1/regions/current` | - | Market, language and timezone resolved for this request |

#### seller

| Method | Path | Auth | What it does |
|---|---|---|---|
| GET | `/api/v1/api/v1/seller/application` | bearer | Full application (wizard + review page) |
| POST | `/api/v1/api/v1/seller/application/directors` | bearer | '+ Add Another Director' |
| DELETE | `/api/v1/api/v1/seller/application/directors/{director_id}` | bearer | Director Delete |
| PATCH | `/api/v1/api/v1/seller/application/directors/{director_id}` | bearer | Director Update |
| GET | `/api/v1/api/v1/seller/application/documents` | bearer | Latest version of each document |
| POST | `/api/v1/api/v1/seller/application/documents` | bearer | Upload or replace a KYC document (PDF/JPG/PNG ≤ 10 MB) |
| POST | `/api/v1/api/v1/seller/application/resubmit` | bearer | Update Application → Resubmit |
| GET | `/api/v1/api/v1/seller/application/status` | bearer | Application Status / progress timeline |
| PATCH | `/api/v1/api/v1/seller/application/steps/banking` | bearer | Step 4 autosave |
| PATCH | `/api/v1/api/v1/seller/application/steps/basic-info` | bearer | Step 1 autosave |
| PATCH | `/api/v1/api/v1/seller/application/steps/business-info` | bearer | Step 2 autosave |
| PATCH | `/api/v1/api/v1/seller/application/steps/representative` | bearer | Step 3 autosave |
| POST | `/api/v1/api/v1/seller/application/submit` | bearer | Review & Submit → Submit |
| GET | `/api/v1/api/v1/seller/store` | bearer | My store |
| POST | `/api/v1/api/v1/seller/store/activate` | bearer | 'Activate & Start Selling' |
| GET | `/api/v1/api/v1/seller/store/slug-available` | bearer | Check a store URL |

#### seller: analytics

| Method | Path | Auth | What it does |
|---|---|---|---|
| GET | `/api/v1/api/v1/seller/analytics/customers` | bearer | Repeat rate, new vs returning per month, top customers |
| GET | `/api/v1/api/v1/seller/analytics/export.csv` | bearer | Any report as CSV |
| GET | `/api/v1/api/v1/seller/analytics/fulfilment` | bearer | Time to ship and deliver, status mix, cancellation/return rates |
| GET | `/api/v1/api/v1/seller/analytics/funnel` | bearer | Views → wishlist → cart → purchase, with the biggest drop-off |
| GET | `/api/v1/api/v1/seller/analytics/overview` | bearer | Gross vs net sales, AOV, repeat/cancellation/return rates |
| GET | `/api/v1/api/v1/seller/analytics/products` | bearer | Best sellers and slow movers |
| GET | `/api/v1/api/v1/seller/analytics/sales` | bearer | Sales Analytics: gross vs net, discounts, refunds, revenue over time, channel and category mix |
| GET | `/api/v1/api/v1/seller/analytics/search` | bearer | What shoppers typed; market searches that found nothing |

#### seller: chat

| Method | Path | Auth | What it does |
|---|---|---|---|
| GET | `/api/v1/api/v1/seller/chat` | bearer | Inbox (premium) |
| GET | `/api/v1/api/v1/seller/chat/summary` | bearer | Unread badge — allowed on every plan; free sellers get counts only |
| GET | `/api/v1/api/v1/seller/chat/{conversation_id}` | bearer | Seller Conversation |
| POST | `/api/v1/api/v1/seller/chat/{conversation_id}/close` | bearer | Mark resolved; a new customer message reopens it |
| GET | `/api/v1/api/v1/seller/chat/{conversation_id}/messages` | bearer | Seller Messages |
| POST | `/api/v1/api/v1/seller/chat/{conversation_id}/messages` | bearer | Seller Send |
| POST | `/api/v1/api/v1/seller/chat/{conversation_id}/read` | bearer | Seller Read |

#### seller: customers

| Method | Path | Auth | What it does |
|---|---|---|---|
| GET | `/api/v1/api/v1/seller/customers` | bearer | Customers table (tabs, search, sort) |
| GET | `/api/v1/api/v1/seller/customers/export.csv` | bearer | 'Export Customers' — CSV |
| GET | `/api/v1/api/v1/seller/customers/overview` | bearer | Customers Overview: KPIs, growth chart, breakdown, recent |
| GET | `/api/v1/api/v1/seller/customers/tabs` | bearer | Customers Tabs |
| GET | `/api/v1/api/v1/seller/customers/{key}` | bearer | View Customer: profile, their orders with this store, notes |
| POST | `/api/v1/api/v1/seller/customers/{key}/message` | bearer | 'Message Customer' — opens or reuses the chat thread (premium) |
| POST | `/api/v1/api/v1/seller/customers/{key}/notes` | bearer | 'Add Note' (private to the store) |
| DELETE | `/api/v1/api/v1/seller/customers/{key}/notes/{note_id}` | bearer | Customer Note Delete |

#### seller: dashboard

| Method | Path | Auth | What it does |
|---|---|---|---|
| GET | `/api/v1/api/v1/seller/dashboard` | bearer | Seller Dashboard: KPIs, revenue chart, performance, latest orders, stock breakdown |
| GET | `/api/v1/api/v1/seller/search` | bearer | Header 'Search anything...' across own products, orders and customers |

#### seller: delivery

| Method | Path | Auth | What it does |
|---|---|---|---|
| GET | `/api/v1/api/v1/seller/delivery-options` | bearer | My delivery options |
| POST | `/api/v1/api/v1/seller/delivery-options` | bearer | Add a delivery option |
| DELETE | `/api/v1/api/v1/seller/delivery-options/{option_id}` | bearer | Remove one (deactivated instead when orders or products reference it) |
| PATCH | `/api/v1/api/v1/seller/delivery-options/{option_id}` | bearer | Delivery Patch |

#### seller: inventory

| Method | Path | Auth | What it does |
|---|---|---|---|
| GET | `/api/v1/api/v1/seller/inventory` | bearer | Inventory table (per product, variants nested) |
| GET | `/api/v1/api/v1/seller/inventory/export.csv` | bearer | 'Export' — CSV, one line per variant |
| GET | `/api/v1/api/v1/seller/inventory/kpis` | bearer | Inventory Kpis |
| POST | `/api/v1/api/v1/seller/inventory/{variant_id}/adjust` | bearer | Adjust stock (logged as a manual movement) |
| GET | `/api/v1/api/v1/seller/inventory/{variant_id}/movements` | bearer | Inventory movements for one variant |

#### seller: orders

| Method | Path | Auth | What it does |
|---|---|---|---|
| GET | `/api/v1/api/v1/seller/orders` | bearer | Orders table (tabs, search, date range) |
| GET | `/api/v1/api/v1/seller/orders/export.csv` | bearer | 'Export' — CSV of this store's orders |
| GET | `/api/v1/api/v1/seller/orders/overview` | bearer | Orders Overview: KPIs, monthly chart, status donut, latest |
| GET | `/api/v1/api/v1/seller/orders/tabs` | bearer | Orders Tabs |
| GET | `/api/v1/api/v1/seller/orders/{order_id}` | bearer | View Order |
| POST | `/api/v1/api/v1/seller/orders/{order_id}/cancel` | bearer | 'Cancel Order' (this store's lines; stock restored) |
| GET | `/api/v1/api/v1/seller/orders/{order_id}/invoice` | bearer | 'Print / Download Invoice' — invoice data (PDF layout is UI-side) |
| POST | `/api/v1/api/v1/seller/orders/{order_id}/status` | bearer | 'Update Status' (processing → packed → shipped → delivered; returned after delivery) |

#### seller: plan

| Method | Path | Auth | What it does |
|---|---|---|---|
| GET | `/api/v1/api/v1/seller/plan` | bearer | Current plan and what it unlocks (drives the sidebar locks and the upgrade card) |

#### seller: products

| Method | Path | Auth | What it does |
|---|---|---|---|
| GET | `/api/v1/api/v1/seller/products` | bearer | Products table (tabs, filters, sort) |
| POST | `/api/v1/api/v1/seller/products` | bearer | '+ Add New Product' → step 1 'Save & Continue' (creates a draft) |
| GET | `/api/v1/api/v1/seller/products/kpis` | bearer | Products KPI cards + tab counts |
| DELETE | `/api/v1/api/v1/seller/products/{product_id}` | bearer | 'Delete Product' (soft; active products must be archived first) |
| GET | `/api/v1/api/v1/seller/products/{product_id}` | bearer | Everything the wizard/edit screen needs |
| PATCH | `/api/v1/api/v1/seller/products/{product_id}` | bearer | Autosave any wizard step (basic info, pricing, shipping…) |
| GET | `/api/v1/api/v1/seller/products/{product_id}/analytics` | bearer | 'View Analytics' |
| PUT | `/api/v1/api/v1/seller/products/{product_id}/content` | bearer | Key Benefits / How to Use sections (PDP tabs) |
| PUT | `/api/v1/api/v1/seller/products/{product_id}/delivery-options` | bearer | Wizard step 4: which delivery options this product supports (empty = all) |
| POST | `/api/v1/api/v1/seller/products/{product_id}/duplicate` | bearer | 'Duplicate Product' → new draft |
| POST | `/api/v1/api/v1/seller/products/{product_id}/images` | bearer | Step 2: upload an image (multipart) |
| PUT | `/api/v1/api/v1/seller/products/{product_id}/images/order` | bearer | Step 2: reorder images (first = primary) |
| DELETE | `/api/v1/api/v1/seller/products/{product_id}/images/{image_id}` | bearer | Image Delete |
| PUT | `/api/v1/api/v1/seller/products/{product_id}/options` | bearer | Step 3: option types → variants regenerated |
| POST | `/api/v1/api/v1/seller/products/{product_id}/status` | bearer | Step 5 'Submit for review' / 'Change Status' / Archive |
| PATCH | `/api/v1/api/v1/seller/products/{product_id}/variants/{variant_id}` | bearer | Step 3: SKU / price / stock per variant |

#### admin · orders

| Method | Path | Auth | What it does |
|---|---|---|---|
| GET | `/api/v1/api/v1/admin/orders` | bearer | Admin Orders |
| GET | `/api/v1/api/v1/admin/orders/{order_id}` | bearer | Admin Order Detail |
| POST | `/api/v1/api/v1/admin/orders/{order_id}/status` | bearer | Advance an order (writes the tracking timeline) |

#### admin · sellers

| Method | Path | Auth | What it does |
|---|---|---|---|
| GET | `/api/v1/api/v1/admin/seller-applications` | bearer | Review queue |
| GET | `/api/v1/api/v1/admin/seller-applications/{application_id}` | bearer | Admin Detail |
| POST | `/api/v1/api/v1/admin/seller-applications/{application_id}/approve` | bearer | Admin Approve |
| POST | `/api/v1/api/v1/admin/seller-applications/{application_id}/documents/{document_id}/review` | bearer | Admin Review Document |
| POST | `/api/v1/api/v1/admin/seller-applications/{application_id}/reject` | bearer | Admin Reject |
| POST | `/api/v1/api/v1/admin/seller-applications/{application_id}/request-info` | bearer | 'We Need More Information' |
| POST | `/api/v1/api/v1/admin/seller-applications/{application_id}/start-review` | bearer | Admin Start Review |

#### admin: chat

| Method | Path | Auth | What it does |
|---|---|---|---|
| GET | `/api/v1/api/v1/admin/chat/flagged` | bearer | Messages that look like off-platform contact details |

#### admin: products

| Method | Path | Auth | What it does |
|---|---|---|---|
| GET | `/api/v1/api/v1/admin/products` | bearer | Review queue (default: pending) |
| POST | `/api/v1/api/v1/admin/products/{product_id}/approve` | bearer | Admin Product Approve |
| POST | `/api/v1/api/v1/admin/products/{product_id}/reject` | bearer | Admin Product Reject |

#### admin: stores

| Method | Path | Auth | What it does |
|---|---|---|---|
| POST | `/api/v1/api/v1/admin/stores/{store_id}/plan` | bearer | Staff: put a store on the premium plan (same as the admin toggle) |

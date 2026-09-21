# Sprint 4 — Multi-country markets (team-lead re-scope, 2026-09-21)

> Status 2026-09-21: implemented (`markets` app, 164 tests). Open questions Q35–Q41 in `docs/data-model.md`.

The team lead reduced new features and asked for three things: (1) a **"dynamic admin"** — every
country gets its own language(s), KYC policy and return policy, edited by staff rather than shipped in
code; (2) **manual KYC** after information/document upload; (3) **automatic language and timezone**
selection from the region (country subdomain or the main domain).

What already existed: manual KYC (sprint 2 — application wizard, versioned document uploads, staff
review API and Django admin). Ask (2) therefore became *"make the existing KYC country-aware"*.

## 1. Decisions (and the alternatives we did not take)

| Decision | Why | Rejected alternative |
|---|---|---|
| **A country is a market.** A store belongs to one country; buyers only see their country's stores; one currency per cart/order. | Every per-country marketplace (Takealot, Daraz, Jumia) works this way; mixed-currency carts, per-country delivery pricing and return law do not compose otherwise. | One global catalogue with country only affecting text — leaves currency, shipping and returns unsolved. |
| **Country subdomains** (`za.asknget.com`, `pk.asknget.com`); the backend reads the host. Explicit `X-Region` / `?region=` for the app and tests. | Explicit, cacheable, SEO-friendly; no guessing. | GeoIP on the backend (MaxMind account, VPN misfires, an extra lookup per request). A GeoIP *suggestion* on the bare domain is an edge/frontend job later. |
| **Language = `X-Language` → `Accept-Language` ∩ country languages → country default.** The stored preference (`User.language`) is returned by `/me` and sent back by the client as a header. | Middleware stays stateless (JWT is resolved inside ninja, not in middleware); response caching can `Vary` on headers. | Decoding the JWT in middleware to read the user's language. |
| **Timezone = `X-Timezone` (validated IANA) → country timezone.** Activated per request; seller portal switches to the *store's* market. | Portal period maths already uses `timezone.localtime()`, so KPIs become market-local automatically. | `User.timezone` column — the browser already knows the zone; cut. |
| **Localised text as per-language JSON** on the policy row (`{"en": …, "ur": …}`) with `markets.i18n.pick()` fallbacks. | Two text fields today; no new package, editable in admin. | django-parler translation tables (right answer if many models become translatable). |
| **One country query per request**, no cache. | Deterministic query budgets; admin edits are visible immediately. | 60 s LocMem cache — poisons test isolation across DB rollbacks and makes budgets order-dependent. |
| **`contextvars` activation** (`markets.context`) like Django's `translation`/`timezone`; `get_country()` is **None outside a request** = unscoped. | 16 `public_products()` call sites need no `request` threading; jobs, signals and fixtures never silently see one market. | An implicit default country outside requests — would hide bugs in tests and write single-market counts from jobs. |

Backend strings stay English this sprint; Django's own translations (validation messages, admin)
activate with the language. Translating our messages needs `makemessages` + translators (Q37).

## 2. Data model additions

| Table | Purpose | Key fields |
|---|---|---|
| `markets_country` | a market | `code` PK ISO-2, `name`, `subdomain` (unique), `currency`, `timezone`, `default_language`, `languages` JSON, `phone_prefix`, `region_label`, `postal_code_regex`, `is_active`, `is_default` (exactly one), `sort_order` |
| `markets_kycpolicy` | per-country KYC rules (1:1) | `mode` (`manual`; `provider` reserved), `required_documents` JSON `{account_type: [doc types]}` (empty = platform defaults in `sellers/rules.py`), `document_labels` i18n per type, `id_number_label`, `id_number_regex`, `tax_id_label`, `company_registration_label`, `instructions` i18n |
| `markets_returnpolicy` | per-country return rule (1:1) | `window_days` (30), `summary` i18n, `url`, `is_active` |

Country on existing tables (all backfilled to `DEFAULT_COUNTRY` by data migrations):

| Table | Field | Rule |
|---|---|---|
| `seller_applications` | `country` FK | set from the request's market at `POST /auth/seller/register`; staff may change it before approval |
| `stores` | `country` FK | copied from the application on approval; **immutable** (read-only in admin) |
| `products` | `country` FK | denormalised from the store; partial index `(country, -published_at) WHERE status='active' AND deleted_at IS NULL` |
| `categories` | `product_counts` JSON | `{"ZA": 128, "PK": 40}` replaces the single `product_count`; `category_tree()` picks the active market |
| `orders` | `country` FK, `return_window_days` | market of the cart; return policy snapshotted at checkout |
| `delivery_methods` | `country` FK | prices are per market; name unique per country |
| `vouchers`, `banners` | `country` FK nullable | blank = every market |
| `users` | `language` | preference; blank = auto |
| `addresses` | `country` | no more `"PK"` default — blank means the market being shopped in (the popup has no country field) |
| `seller_documents.type` | +`tax_registration`, `business_licence`, `other` | so a policy can require them |

No `Cart.country`: a cart's market is its first line's (`orders.services.cart_country_id`), or the request's
market while empty.

## 3. Request resolution (`markets.middleware.RegionMiddleware`, `/api/` only)

```
country   X-Region | ?region=  →  first host label == Country.subdomain  →  default country
language  X-Language | ?lang=  →  Accept-Language ∩ country.languages       →  country.default_language
timezone  X-Timezone (IANA)    →  country.timezone
```

- Unknown/inactive **explicit** region → `400 {"detail": "unknown_region"}`. Unknown host labels
  (`localhost`, container names, IPs) fall through to the default.
- Sets `request.country`, `request.language`, `request.timezone`; response carries `Content-Language`,
  `X-Region` and `Vary: X-Region, X-Language, X-Timezone, Accept-Language`.
- Everything is reset in `finally` (gunicorn threads and the test client reuse threads).
- `portal.auth.StoreAuth` re-points the request at the **store's** market.

Public endpoints: `GET /regions` (active countries for the switcher), `GET /regions/current` (resolved
market, language, timezone, currency, postal regex, localised return policy). `GET /me` returns the
stored `language` plus the resolved `locale`.

## 4. Storefront, cart and checkout rules

- `catalog.services.public_products(country=CURRENT)` scopes every list, PDP, filter facet, search,
  suggestion and recommendation rail. `None` = all markets (jobs, category counts). Wishlist items from
  another market are hidden while browsing that market. `frequently_bought_together`'s cache key is not
  country-keyed on purpose — `_ordered()` re-filters through `public_products()`.
- Cart: a second market's product → `409 cart_region_mismatch`; the UI offers "clear cart and switch"
  (`DELETE /cart/items`). A guest cart merged on sign-in drops lines from another market. Delivery methods
  and vouchers are filtered by the cart's market (`422 Unknown delivery method`, "not valid in your region").
- Checkout: blank address country = the cart's market; a different country → `409 ships_outside_region`;
  `Order.country` and `Order.return_window_days` are snapshotted; the buyer order detail exposes
  `country`, `return_window_days`, `return_eligible_until` (last delivery event + window).
- Seller portal: new products get `country = store.country`, `currency = store.country.currency`; the
  dashboard reports in the store's currency and timezone from any domain.

## 5. KYC per country (`sellers/policy.py`)

`policy_for(application)` = platform defaults (`sellers/rules.py`) overlaid with the application
country's `KycPolicy`. Only the **document list, labels, ID format and instructions** are per country;
required steps and field lists stay per account type (Q7). A document whose natural step the account
type skips (an individual's NTN certificate on the Representative step) is collected on the last step
they fill in. `ApplicationOut.policy` and `GET /lookups/seller?country=&account_type=` return the same
block; `PATCH …/basic-info` / `representative` validate ID numbers against `id_number_regex`
(422 with the country's label). The staff queue takes `?country=`.

Seeded examples (`seed_lookups`): **ZA** — SA ID / Passport, CIPC, VAT, 30-day returns, en/af/zu,
Africa/Johannesburg, ZAR. **PK** — CNIC / Passport, SECP, NTN (companies also upload the NTN
certificate), 7-day returns, en/ur, Asia/Karachi, PKR.

## 6. Headers and error codes (for the frontend)

| Header | Direction | Meaning |
|---|---|---|
| `X-Region: PK` | request | force a market (else the subdomain decides) |
| `X-Language: ur` | request | the user's saved preference (`/me.language`) |
| `X-Timezone: Asia/Karachi` | request | browser zone for date maths |
| `X-Region`, `Content-Language` | response | what the request resolved to |

| Code | Where | Meaning |
|---|---|---|
| `400 unknown_region` | any `/api/` call | explicit region unknown or inactive |
| `409 cart_region_mismatch` | `POST /cart/items` | product belongs to another market |
| `409 ships_outside_region` | `POST /checkout` | shipping address country ≠ cart market |
| `422 Unknown delivery method` | cart / checkout | method belongs to another market |
| `403 no_active_store` | `/seller/*` | unchanged |

## 7. Not in this sprint

GeoIP redirect on the bare domain, KYC provider integration (`KycPolicy.mode = provider`), translation of
backend strings, per-country staff roles (country managers), the buyer return-request flow, multi-currency
inside one country, per-country `SiteSetting`, `User.timezone`, and the paused performance steps 2–6.

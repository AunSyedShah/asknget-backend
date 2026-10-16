# API changes: what the frontend must react to

> Maintained by the **backend session**. Newest first. One entry per change that touches the contract (`openapi.json`).
> **additive** = a new endpoint or field; adopt it when you like. **BREAKING** = an existing shape changed; the entry says what to do.
> Everything before 2026-10-14 is described in [`frontend-handoff.md`](frontend-handoff.md) sections 4 to 5a. Regenerate your API types from
> `openapi.json` on Preview whenever there is a new entry. Last endpoint count: **234 operations**.

## 2026-10-16

- **Answers to `BACKEND_ASKS.md`** (ask #5, the admin portal API, is a separate sprint and not in this batch). **Not on Preview yet**: committed on `develop`, live with the next push; this note is then removed.
  - **BREAKING for generated type names only (ask #2); no response or request body changed.** Fifteen schema classes shared a name with another class, so `openapi.json` merged them and a generated client got one of the two shapes wrong. Each now has its own name, and a test fails if two share one again. Old to new (wire format identical):
    - cart / checkout: `DeliveryOptionOut` → `CartDeliveryOptionOut`, `DeliveryRuleOut` (the buyer's rule in the cart) → `CartDeliveryRuleOut`, `AddressIn` (checkout) → `CheckoutAddressIn`. The seller's `DeliveryRuleOut` (fields `locked`, `source`, `own`, `currency`) keeps its name; `AddressIn` of the address book keeps its name.
    - seller portal: `OptionTypeOut` → `SellerOptionTypeOut`, `RejectIn` (product) → `ProductRejectIn`, `TimelineStageOut` (orders) → `SellerOrderTimelineStageOut`.
    - returns: `RejectIn` → `ReturnRejectIn`. Seller applications: `CategoryRefOut` → `ApplicationCategoryOut`, `TimelineStageOut` → `ApplicationTimelineStageOut`, `RejectIn` → `ApplicationRejectIn`.
    - chat / notifications / site: `MessageOut` (chat) → `ChatMessageOut`, `StoreRefOut` (chat) → `ChatStoreRefOut`, `SummaryOut` → `NotificationSummaryOut`, `ContactIn` (contact form) → `ContactFormIn`, `ChoiceOut` → `SiteChoiceOut`; the category-request parent `CategoryRefOut` → `RequestedParentOut`.
    - The names that kept their name are the common ones: `CategoryRefOut` (with `slug`), `StoreRefOut` (with `display_name`), `OptionTypeOut` (with `id`, `values`), `MessageOut` (`{detail}`), `ContactIn`, `AddressIn`.
  - **Review title (ask #7, additive):** `POST /products/{slug}/reviews` accepts an optional `title` (max 120, default empty); `ReviewOut` returns `title`.
  - **Seller-application list shape (ask #4, additive):** `GET /admin/seller-applications` now answers `{items, total, page, per_page, pages, count}` and takes `page`/`per_page`. `limit`/`offset` and `count` still work, so nothing breaks.
  - **Banners (ask #3):** `BannerOut.placement` is a declared enum (`hero`, `top_discount`, and the rest); an unknown `?placement=` is a 422.
  - **Per-market contact details (ask #6, additive):** `GET /site-info` overlays the market's own `contact_phone`, `contact_email`, `address` and `social` over the platform-wide ones, so ZA and PK differ. Edited in Django admin on the country.
  - **Demo pictures and banners (ask #6):** the staging data gets product galleries, category pictures, five hero slides and four promo tiles. Image URLs are full `https://` links (placeholder photos); real uploads still need object storage, which is a separate decision.

- **Fixed: a brand-new guest's first `POST /cart/items` (and every other cart-changing call) answered with an EMPTY cart.** The item was saved, but the response was built from a second, empty cart (and left an orphan cart row behind). Every cart call now answers with the cart it changed, so `cart_token` and `items` in the first response are right. If you worked around it by calling `GET /cart` again after the first add, you can stop. **Live on Preview since 2026-10-16** (verified: a new guest's first add returns the item, the same cart answers the next calls).
- **The OpenAPI document now declares the shared request headers and the error body** (additive, documentation only; nothing changes at runtime). **Live on Preview since 2026-10-16** (verified: 234 operations, headers declared, `ErrorResponse` present).
  - `X-Region`, `X-Language`, `X-Timezone` on every operation; `X-Store` on `/seller/*`; `X-Cart-Token` on `/cart*` and `/checkout*`. All optional; defined once under `components.parameters`.
  - **A bug fixed in the document:** the cart token was declared as `x_cart_token` (underscores), which a generated client would send literally and servers and proxies drop. It is `X-Cart-Token` now; the server always accepted that.
  - Every operation has a `default` response typed `ErrorResponse` (`{detail, errors?}`). `info.description` says the base URL is the origin only (paths already start with `/api/v1`).
  - Regenerate your client after Preview has it; if you wired `X-Cart-Token` by hand, you can drop that.
- **Preview and Production are separate environments.** Preview (`develop`) reads the staging database with demo data; Production (`main`) is empty. Build against Preview only. Swagger is public on both.
- **CORS on Preview now allows `http://localhost:3000` and `http://127.0.0.1:3000`** (so a browser page there can call Preview directly; exact origins, so another port such as 5173 is still refused). **Production allows no browser origin** and never will for localhost: develop against Preview. Browser calls must not use credentials (`credentials: 'include'`); the allowed request headers are `Authorization`, `Content-Type`, `Accept-Language`, `X-Region`, `X-Language`, `X-Timezone`, `X-Cart-Token` and (after the next push) `X-Store`; `X-Region` and `Content-Language` are readable on responses. Another port or a deployed frontend: ask the owner, who has the backend session add the origin. The proxy approach in the brief needs none of this and also works on deployed sites.
- **A bug fixed in the CORS settings:** `X-Store` (seller calls for a person with several stores) was missing from the allowed request headers, so a browser call carrying it was blocked at the preflight. Added; test added. **Live on Preview since 2026-10-16** (verified: the preflight for `X-Store` is allowed from `http://localhost:3000`).

## 2026-10-15

- **Category requests** (additive). A seller cannot create categories; they ask and staff decide. **Live on Preview since 2026-10-16** (all seven paths are in the OpenAPI document).
  - Seller: `POST /seller/category-requests` (multipart: `name`, `reason`, `parent_id?`, `document?`), `GET /seller/category-requests`, `GET` and `DELETE /seller/category-requests/{id}` (withdraw).
  - Staff: `GET /admin/category-requests`, `GET /{id}`, `POST /{id}/approve`, `POST /{id}/reject`.
  - Errors to handle: `409 category_exists` (with the category to use instead), `409 already_requested`, `409 not_pending`, `422 parent_must_be_top_level`, `429` (5 waiting per store).
  - New notification kinds `category_request_approved` and `category_request_rejected` (System tab).
  - Details: `frontend-handoff.md` section 5, "Category requests".
- **Categories seeded**: the tree on Preview now has **14 top-level categories and 54 subcategories** (was 9 and 12). No ids changed. `GET /categories` is unchanged in shape.

## 2026-10-14

- **Delivery fee rule: free over an amount, otherwise a fee** (additive, plus one type change for the seller portal).
  - Cart: each `deliveries[]` entry gains `rule {free_over_cents, fee_cents, store_subtotal_cents, remaining_cents, free}`; each option gains `waived` and `follows_rule`. Prices are unchanged until a rule is set.
  - Seller: `GET`, `PUT`, `DELETE /seller/delivery-options/rule` (`409 rule_locked` while the admin locked the market).
  - **Type change:** in the seller's delivery options (`GET`, `POST`, `PATCH /seller/delivery-options`), `price_cents` is now `integer | null`. `null` means "follows the rule"; it can only be sent for a delivery option, not pickup (`422`). Handle `null` when displaying.
  - Orders keep what they were charged; nothing on an existing order changed.
- **Phone verification by WhatsApp** (additive).
  - `POST /auth/whatsapp/start` returns `{id, link, message, whatsapp_number, expires_at}`: draw `link` as a QR on desktop and open it from a button on a phone. Poll `GET /auth/whatsapp/{id}` every 2 to 3 seconds until `status` is `verified`, `failed` or `expired`.
  - Errors: `429` (5 tries an hour), `503` when WhatsApp is not configured, `422` for a number that is not the account's.
  - Today it works only against Twilio's **sandbox**: each tester sends `join <word>` to the sandbox number once first.
  - Details: `frontend-handoff.md` section 5, "Phone verification by WhatsApp".
- **Email OTP now really sends** on Preview (`POST /auth/otp/send`, `/otp/verify`; unchanged shapes). Production has no mail settings yet and prints codes to its log instead.

## Known issues (backend session will fix; do not work around silently)

- **Uploads fail on Vercel** (read-only disk; no object storage yet): avatar, product images, review and return photos, KYC documents, the category-request image.

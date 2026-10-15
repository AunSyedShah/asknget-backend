# API changes: what the frontend must react to

> Maintained by the **backend session**. Newest first. One entry per change that touches the contract (`openapi.json`).
> **additive** = a new endpoint or field; adopt it when you like. **BREAKING** = an existing shape changed; the entry says what to do.
> Everything before 2026-10-14 is described in [`frontend-handoff.md`](frontend-handoff.md) sections 4 to 5a. Regenerate your API types from
> `openapi.json` on Preview whenever there is a new entry. Last endpoint count: **234 operations**.

## 2026-10-16

- **Preview and Production are separate environments.** Preview (`develop`) reads the staging database with demo data; Production (`main`) is empty. Build against Preview only. Swagger is public on both.
- **CORS is not set for any browser origin yet.** The frontend's `http://localhost:<port>` is added once the owner confirms the port (this file will say when).

## 2026-10-15

- **Category requests** (additive). A seller cannot create categories; they ask and staff decide. **Not on Preview yet**: it is committed on `develop` and goes live with the next push; this note is removed then.
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

- The first `POST /cart/items` from a brand-new guest answers with an **empty cart**, although the item is saved; read `GET /cart` with the returned `cart_token` and the item is there.
- **Uploads fail on Vercel** (read-only disk; no object storage yet): avatar, product images, review and return photos, KYC documents, the category-request image.

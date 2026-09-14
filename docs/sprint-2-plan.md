# Sprint 2 — remaining endpoints

Baseline: models + migrations for every app exist; only `accounts` has an API.
Design baseline: Figma snapshot 2026‑09‑11 (`docs/figma/manifest.json`).

**Explicitly out of scope this sprint** (per team lead, 2026‑09‑14):
- Payment provider integration — checkout records the chosen method and leaves the
  order `pending_payment` / `unpaid`; the PSP hook lands next sprint.
- Vendor sub-roles / permissions and premium-vendor chat — not in Figma yet.

## Slices (in delivery order)

| # | Slice | Endpoints | Screens |
|---|-------|-----------|---------|
| 1 | **Seller onboarding** | `GET /seller/application`, `PUT …/steps/{1-4}`, directors CRUD, documents upload/replace, `POST …/submit`, `POST …/resubmit`, `GET …/status`, store `slug-available` + `activate`, `GET /lookups/*` | Basic Info → Review & Submit, Application Submitted/Status, More Info Required, Update Application, Approved, Activate |
| 2 | **Admin KYC review** | `GET /admin/seller-applications`, `GET …/{id}`, `POST …/{id}/start-review` · `request-info` · `approve` · `reject`, document accept/reject | none designed — admin "does KYC and has all access" |
| 3 | **Catalog (public)** | categories tree, brands, products list (filters/sort/pagination), product detail, related, curated lists, reviews list/create, wishlist | Landing, Categories, Product Details ×3, Wishlist |
| 4 | **Cart & checkout** | cart CRUD, voucher apply/remove, delivery methods, `POST /checkout` (no PSP) | Cart, Checkout, Order Placed |
| 5 | **Orders** | `GET /me/orders?tab=`, detail, cancel, reorder, public `POST /orders/track` | My Orders ×4, Track Order |
| 6 | **Marketing / support** | `POST /contact`, `POST /newsletter/subscribe`, `GET /banners` | Contact Us, footer, Landing banners |

Each slice ships as `schemas.py` + `services.py` + `api.py` + `tests.py`, is
mounted in `asknget/api.py`, and updates the developer guide's status table.

## Decisions taken (change here first if the flow changes)

**Seller onboarding**
- One application per user, created at `/auth/seller/register`. Steps 1–4 are
  independent `PUT`s (autosave, partial allowed); each step's *completeness*
  rules live in one table `sellers/rules.py` so per-account-type differences are
  one edit away.
- Per account type: `individual` → representative step optional (defaults to the
  applicant), company-registration document not required. `sole_proprietor` and
  `registered_company` → all four steps + all four documents. ❓ §10 Q7.
- Status machine: `draft → submitted → under_review → (more_info_required ⇄
  resubmitted) → approved | rejected`; `approved → active` on store activation.
  Every transition writes a `SellerApplicationStatusEvent` (feeds the timeline).
- Documents: uploads create a new `SellerDocument` version; the previous one is
  linked via `superseded_by`. PDF/JPG/PNG ≤ 10 MB (design copy).
- Resubmit requires every open `SellerInfoRequestItem` resolved (document
  replaced or field-group touched since the request) + "I confirm" flag.
- Timeline is the five fixed stages from the Application Status screen
  (Submitted · Under Review · More Info · Approved · Active); "More Info" is
  rendered as `skipped` when it never happened. ❓ design shows it as always
  present — confirm.
- Store activation: slug derived from display name, must be unique; requires
  both agreement checkboxes; sets application `active`, store `active`.

**Admin**
- `StaffAuth` = valid JWT + `is_staff`. No separate role table yet (three roles
  are `is_staff` / `is_seller` / neither); sub-roles deferred.

**Catalog / cart / orders**
- Authenticated cart only for now (guest cart deferred with Q2). Cart lines
  reference variants; every product gets at least one default variant.
- Checkout snapshots addresses, delivery method and prices onto the order;
  `payment_status=unpaid`, `status=pending_payment`; stock is reserved at
  checkout. Bank transfer / card / wallet just record `payment_method_type`.
- Orders tabs: `ongoing` = confirmed/processing/shipped/out_for_delivery (+
  pending_payment), `completed` = delivered, `cancelled` = cancelled/refunded.
- Cancellation allowed while status ≤ `processing`. ❓ Q4.
- Reviews: authenticated users only, `pending` until approved. ❓ Q5.

## Open questions carried forward
See `docs/data-model.md` §10. New this sprint: More-Info stage always shown?;
who moves `submitted → under_review` (auto on admin open vs explicit button).

# Product review flow: draft plan (single and bulk, checks in the background)

The owner's flow, 2026-10-27: a seller or vendor adds a product, **one at a time or in bulk**. The product is **inserted as not
published**, the seller sees a message that it is **under review**, and the **automatic checks run in the background**. This plan
ties together [`ai-product-moderation-plan.md`](ai-product-moderation-plan.md) (what the checks are) and
[`product-csv-import-plan.md`](product-csv-import-plan.md) (bulk) into one lifecycle, after a read of the code that this
flow touches. Section A is what the code does today; several findings change the design.

## Progress

| Phase | Status |
|---|---|
| 0 Make the existing flow trustworthy | **Built 2026-10-27, not yet pushed or deployed** (see "Phase 0 as built" below). Findings F1-F6 and F12-F14 are closed; F9 needed no change; F7, F8 and F10 belong to Phase 1 |
| 1 Flow engine with a manual decider | next |
| 2 The checks of the moderation plan plug in | after 1 |
| 3 Bulk CSV import | after 1 (CSV-1 can start earlier) |
| 4 Edit re-checks by the engine, reports, stats | after 2 |

### Phase 0 as built (differences from the sketch in sections B and E are marked)

- **`catalog/review.py` (new)** holds the fingerprint (`content_hash`), `stamp` (record an approval), `note_edit` and the `tracked_edit`
  decorator that every seller edit of a **live or published-then-archived** product goes through. **Difference:** the price signature is folded
  into the one fingerprint (no separate `reviewed_price_sig` column), and there is **no stored `content_hash` column**: the edit is judged by
  comparing the fingerprint before and after the change, and the result is kept as the boolean `edited_since_review`.
- **Products published before this existed** have an empty `reviewed_hash`; their first edit adopts the content *as it was before that edit*
  as the approved version, so no data migration is needed and nothing is flagged by the deploy itself.
- **Product columns added** (migration `catalog 0010`): `reviewed_hash`, `edited_since_review` (indexed), `review_source`, `reviewed_by`, `reviewed_at`.
- **F1 closed:** pictures (add, delete, reorder), content sections and variant **price** answer `409` while `pending`, like the name and the
  options did. Stock, SKU and the variant on/off switch stay editable (a review does not read them; they are not in the fingerprint).
- **F2 closed in the safe-for-the-frontend way:** `PRODUCT_EDIT_POLICY=flag` (default): the edited live product **stays live** and is listed for staff
  (`GET /admin/products?status=edited`, rows carry `edited`), who approve the edit again or take the product down with a reason. `hold` hides it
  (`pending`) until approved. I chose `flag` as the default because the wizard autosaves on every step, and flipping the status on the first
  autosave would answer `409` to the next one. **This is decision K3**, still the owner's; the Phase 1 engine decides these edits automatically.
- **F3 closed:** unarchiving a product that was changed while archived returns it to `pending`, not `active`.
- **F4 closed:** `approve` and `reject` take the deciding user (`actor`; none = the system), record `review_source`, `reviewed_by`, `reviewed_at`
  (and the approved fingerprint), refresh the category counts, and **send the seller notification themselves**, so every path notifies. Re-approving an
  edit does not say "is now live" again.
- **F5 closed:** category counts refresh on every move into or out of the shop window (archive, unarchive, unpublish, take-down, approve).
- **F6 closed:** `change_status`, `approve` and `reject` lock and re-read the product row; a second submit from a stale copy answers `409`.
- **F12 closed:** `is_featured` and `is_new_arrival` are removed from `ProductPatchIn` and ignored by the service; sellers still see them. Staff set
  them in Django admin until an admin API exists (`BACKEND_ASKS` #5).
- **F13 closed:** `currency` in `PATCH` must be the store market's currency (`422` otherwise); it is not a way to change it.
- **F14 closed:** `duplicate()` excludes all review columns, so a copy is an unreviewed draft (and still has no pictures).
- **Tests:** `portal/test_review_trail.py`, 22 tests, one or more for each finding, plus the hash itself (spacing, case, real changes).
- **Contract changes** are in `api-changes.md` (2026-10-27); `data-model.md` and `environment.md` are updated.

## A. What the code does today (research, 2026-10-27)

**Lifecycle (`catalog/models.py`, `portal/products.py`).** `draft → pending → active | rejected`, plus `archived`. The wizard creates a
draft with one default variant (`POST /seller/products`); the seller edits it in steps; Step 5 `POST /seller/products/{id}/status
{action: submit}` checks completeness (`completion()`: name, category, at least one image, price > 0, a variant, stock or pre-order) and
sets `pending`. Staff `POST /admin/products/{id}/approve|reject` (`products.review`, market-scoped) sets `active` or `rejected`.
The seller's list tabs are the fixed set `active | pending | draft | rejected | archived`.

**Everything shoppers see goes through one door.** `catalog.public_products()` filters `status='active'` and the store `active`, and the
cart and checkout use `orders.services._variant_purchasable()` with the same rule. Search, recommendations, wishlist, related products,
brands and category counts all start from `public_products()`. **So any other status (a new `suspended`, `pending`, `draft`) is
automatically invisible and unbuyable.** The "does checkout refuse a non-active product?" question from the earlier plans is answered: yes.

**Findings that matter for this flow (each is a gap in today's manual flow too):**

| # | Finding | Where | Why it matters |
|---|---|---|---|
| F1 | Only `PATCH` and `PUT options` refuse edits while `pending`. **Images, content (benefits/usage), variants and delivery-options can be changed on a product under review.** | `portal/products.py` `add_image`, `delete_image`, `set_content`, `patch_variant` | A product can be changed *after* it was checked and before it is approved |
| F2 | **An `active` product can be edited freely with no re-review** (price, name, description, images). | `patch()` only blocks `pending` | Approve something innocent, then change the text: the check is meaningless without a re-check |
| F3 | **`archive` then `unarchive` returns a product straight to `active`** when it has a `published_at`, and an archived product can still be edited. | `change_status` `unarchive` | A second route past review |
| F4 | `approve()` and `reject()` record **neither who nor when**; the seller notification is sent from the API function, not the service. | `portal/api.py` 228-245; `portal/products.py` | A system decision must record itself and notify, so it must go through the same path |
| F5 | `refresh_category_counts()` runs **only on approve**. Archive, unpublish and delete of a live product leave "128 items" stale. | `portal/products.py` | Auto-publish and take-downs would make this visibly wrong |
| F6 | `change_status` has **no row lock**: two submits at once race. | `@transaction.atomic` only | A background job must claim work atomically (the outbox already does this with a conditional update) |
| F7 | Seller notices are the legacy `notify()` kinds `product_approved` and `product_rejected` (store level, tab "System"), **not** the Sprint 9 event bus. Staff get nothing when something needs review. | `notifications/services.py`, `defaults.py` | New outcomes ("held for a person", "suspended") and the staff alert belong on the bus |
| F8 | Staff cannot tell who decided; the admin queue shows `pending` only by default. | `review_queue` | Needs `decided_by` and an audit view of what the system approved |
| F9 | **Search after publish (checked, low risk).** `hybrid_search` has a fallback: a product with no embedding row yet still matches on **name, short description and brand** by substring. The keyword and semantic parts need the embedding row, which `embed_products --watch --interval 30` (the compose `worker`) creates within about 30 seconds. | `discovery/search.py` lines 121-141, `compose.yaml` | A product approved in two seconds is findable by name at once and by tags, description and benefits within about 30 s. **No change needed**; embedding at publish time is an optional nicety |
| F10 | **Background work already has a home on the EC2 stack:** `compose.yaml` runs `worker` (`embed_products --watch`) and `notifier` (`deliver_notifications --watch`, several copies allowed because messages are claimed first), plus `web`, `realtime`, `redis`, `db`, `proxy`. nginx allows 30 MB bodies and gunicorn a 60 s timeout. | `compose.yaml`, `deploy/nginx.conf` | A `reviewer` service next to them is the natural place for the checks (section D). No cron or external pinger is needed |
| F11 | Seller payload has `status`, `rejection_reason`, `completion`, `public_url`; the frontend keys tabs on the five status names. The Figma **admin** Products screens already ask for a **Suspended** tab and a **suspend action** (`BACKEND_ASKS.md`, Products and Product Detail), so `suspended` is a wanted concept. Whether the **seller** screens tolerate an unknown status or tab key cannot be checked from here (see L). | `serialize()`, `docs/frontend-handoff.md`, `BACKEND_ASKS.md` | New information is **additive**; `suspended` needs the frontend's confirmation on the seller side |
| F12 | **Sellers can set `is_featured` and `is_new_arrival` on their own products** (`ProductPatchIn`), and the storefront's Featured and New Arrivals lists read those flags. They are not part of any review. | `portal/schemas.py` 135-136, `catalog/api.py` 81-91 | Self-promotion without approval; it should be staff-only or part of the checks |
| F13 | **Sellers can change a product's `currency`** (`ProductPatchIn.currency`, uppercased and stored), while it is created from the store's market currency. | `portal/products.py` `patch`, `create` | A price typed in the wrong currency defeats price sanity checks and could mislead buyers; it should follow the store's market |
| F14 | **`duplicate()` copies every product column except a fixed list.** Any column added for this flow (`reviewed_hash`, `reviewed_by`, `review_source`, `reviewed_at`, `import_key`) would be copied onto the duplicate: a copy would arrive already "approved", or break the unique import key. It does **not** copy images or delivery options, so a duplicate is never ready to submit without new pictures. | `portal/products.py` `duplicate` | The exclusion list must be extended in Phase 0, with a test that a duplicate starts unreviewed |

**Reusable pieces:** `portal.products.approve/reject/change_status` (the decision appliers); `catalog.public_products` (no change
needed for hiding); the notification bus and `NotificationRule`s (admin-editable); `team.log` (activity feed: "Submitted: NARS BB Cream");
`StaffAuth("products.review")` and market scoping; the outbox pattern for retries; the WhatsApp-verification poll contract
(frontend already polls `GET .../{id}` every 2-3 s) as the model for "wait for the result".

## B. The lifecycle we will have

```
            seller adds (wizard) ─────────────┐                         CSV import (bulk) ─────┐
                                              ▼                                               ▼
   draft  ── Submit (all rows complete) ──▶  pending  ◀── material edit of a live product ── active
 (not published,        "Under review"         │  review.state = checking          (re-check; see section E)
  being edited)                                │      │
                                               │      └─ clean ───────────────────────────────▶ active  (decided_by = system)
                                               │      └─ any doubt ─▶ review.state = waiting_for_person ─▶ staff ─▶ active | rejected
                                               └─ hard violation (if auto-reject is on) ───────▶ rejected  (reason, can appeal)
                       active ── taken down by a check, a report or staff ──▶ suspended  (reason, can appeal)
                       active | draft ◀──▶ archived
```

- `draft` is the seller's workspace: not published, editable, **no automatic check runs** (except the instant rules pre-check
  that tells the wizard what is wrong).
- `pending` is the single "**Under review**" status. It is **locked for editing** (all endpoints, closing F1). Its `review.state`
  says whether the system is checking (`checking`, normally seconds) or a person has it (`waiting_for_person`).
- **No new status is needed except `suspended`.** I recommend against a separate `checking` status: it would force the frontend to
  learn a new tab for something that lasts seconds.
- The product is **published only by a decision**: system or staff. There is no path to `active` that skips a recorded decision
  (section E closes F2 and F3).

### The invariant that makes this safe

> A product may be `active` only if the **content hash** it has now equals the hash that was approved (`Product.reviewed_hash`).

**Exactly what the hash covers** (settled from the editable fields in `ProductPatchIn`, `set_options`, `set_content` and the image endpoints):

| In the hash | Not in the hash (editing it never triggers a check) |
|---|---|
| `name`, `short_description`, `description_title`, `description`; `tags` (sorted); `category_id`, `brand_id`; `barcode`; option type names and values (sorted); benefit and usage-step sections (title and body, in order); the product's image **file names** | stock and variant stock, SKUs, `low_stock_threshold`, `preorder`; weight, size, `ships_from`, `handling_days`; delivery options; `cost_price_cents`; the featured and new-arrival flags (they leave the seller's hands, F12); variant `is_active` |
| **Price signature** (folded into the same fingerprint): `floor(log2(lowest effective price))` and the discount percentage in steps of 10 | A price change inside the same band (a typo fix from 120.00 to 119.00) |

Notes: image file names are random per upload (`safe_image` names them `<uuid>.<ext>`), so a new upload is a changed image and
re-ordering is not; text is normalised (trimmed, case and whitespace folded) before hashing so a stray space is not a change. A price
that doubles or halves, or a discount that jumps (the compare-at trick), changes the signature and is checked. Any transition into or inside `active` (approve, unarchive, edit) compares the two. A mismatch means
**run the checks first**. This one rule closes F2 and F3 without listing every endpoint, and it also stops a stale check from
approving changed content: a decision is applied only if the product is still `pending` **and** its hash still equals the one that
was checked; otherwise the result is discarded as stale.

## C. What the seller sees

- After **Submit** (or after a bulk upload) the response is the usual product body with `status: "pending"` and a new additive block:

```json
"review": {
  "state": "checking",                     // none | checking | waiting_for_person | approved | rejected | suspended
  "message": "Your product is under review. This usually takes a few seconds. We will tell you when it is live.",
  "since": "2026-10-27T09:14:02Z",
  "decided_by": null,                      // "system" | "staff" once decided
  "reasons": [],                           // seller-safe, from reason codes: [{"code": "contact_details", "message": "Phone numbers are not allowed in descriptions"}]
  "can_edit": false,
  "can_withdraw": true,
  "can_appeal": false
}
```

- **While it is checking:** the message above; the frontend polls `GET /seller/products/{id}` every 2-3 seconds (as for the WhatsApp check)
  until `review.state` changes, with a back-off to 30 s after a minute.
- **Held for a person:** `waiting_for_person`, message "A member of our team is reviewing this product. This can take up to
  {hours} hours" (the hours come from the market policy), plus a bell notification once.
- **Approved:** `status: "active"`, `public_url` set, bell notification "is now live" (already exists: `product_approved`).
- **Rejected or suspended:** the reasons in plain words, `can_edit: true`, `can_appeal: true`, bell and e-mail.
- **Bulk:** the job report lists every row as `under review`, then updates to live / waiting / rejected as results arrive. One summary
  notification at the end of the batch, not one per product (a 300-product upload must not create 300 bells).
- The list endpoint rows gain `review_state`; the tab counts stay the same keys (`pending` covers both sub-states) plus an additive
  `suspended` key.

## D. Running the checks "in the background"

**Deployment decision (owner, 2026-10-27): the first deployment is AWS EC2** with the existing `compose.yaml` stack, not Vercel. That
makes this simple: there is a real long-running worker, so the checks run in the background in the proper sense. (Preview on Vercel
remains a demo environment and uses the in-request fast path below.)

The checks in the moderation plan (rules, small models, similarity) take **milliseconds** on CPU, so the design is a **durable record
of work, finished by a small service**, with a fast path for latency:

1. **Submit** (one transaction): lock the product row, set `pending`, write a `ProductCheck(status=queued, content_hash)` and the
   activity line. Return at once with `review.state = checking`.
2. **The `reviewer` service** (new, next to `worker` and `notifier` in `compose.yaml`): `python manage.py moderate_products --watch
   --interval 2` claims queued checks and decides them. Several copies may run. With a 2-second interval the seller normally sees the
   result on the second or third poll. Optionally the claimed work can be woken at once through Redis, which is already in the stack.
3. **Fast path (optional, `MODERATION_RUN=inline`):** right after the commit, the request itself processes that one check within a
   short time budget, so most products are decided before the response is sent. It is how local development and tests run without a
   worker, and how a Vercel Preview behaves. With the worker present it is not required, and it can be switched off to keep request
   latency flat.
4. **Safety net:** the product read endpoint processes that product's own check if it has been `queued` longer than a few seconds
   (a dead worker never leaves a seller waiting).
5. **Claiming is atomic** (conditional update `queued → running`, the pattern `Delivery` already uses), so the worker copies, the fast
   path and the safety net never process one check twice; a stuck `running` check is released after a timeout; each check has a
   retry limit, after which the product goes to a person (**fail closed**).
6. **Bulk** uses the same unit: each CSV row creates a product and a queued check, and the `reviewer` works through them; the
   import job's progress is the count of decided rows. Nothing depends on one long request (gunicorn's timeout is 60 s).

## E. Decision applying (one code path for everyone)

`approve(product, *, actor)` and `reject(product, reason, codes, *, actor)` become the **only** ways to leave `pending`, whether the
actor is a staff user or `system`. Each: re-checks the status and the hash under a row lock (closing F6), writes `reviewed_by`,
`review_source` and `reviewed_at`, refreshes category counts, records the decision on the `ProductCheck` and `team.log`, and emits the notification events. `refresh_category_counts()` is called
from **every** transition into or out of `active` (F5). The staff API calls the same functions, so a staff decision and a system
decision are indistinguishable except for the recorded actor.

**After publishing, the invariant applies:**

- **Edit of a live product** (`PATCH`, images, content, options, variants): if the content hash changes, run the checks **inline**
  (milliseconds). Clean → the product stays live and `reviewed_hash` moves; anything else → it goes back to `pending` (hidden) with
  `waiting_for_person` and the seller is told why. Cosmetic edits (stock, shipping, delivery options) do not change the hash.
  Autosave fires on every wizard step, so the check runs only when the hash changed since the last check, never twice for the same text.
- **Unarchive:** goes live only if the hash equals `reviewed_hash`; otherwise it goes to `pending` and is checked (closing F3).
- **Withdraw** (the existing `unpublish` of a pending product) cancels the check; a result that arrives later is discarded as stale.
- **Suspend** (checks, customer report, staff): status `suspended`, `reasons`, category counts refreshed, placed orders untouched.

## F. Data model (small, additive)

- `catalog.Product`: `reviewed_hash`, `review_source` (`staff|system`), `reviewed_by` (nullable user), `reviewed_at`; status gains `suspended`.
- `moderation.ProductCheck` (as in the moderation plan) with `status` (`queued|running|done|failed|stale`), `content_hash`, `attempts`,
  `locked_at`, `trigger` (`submit|edit|unarchive|import|rescan|report|appeal|sample`), `decision`, `decided_by`, the signals and reason codes.
- `moderation.ModerationLabel`, `ModerationPolicy`, `ModelVersion`: unchanged from the moderation plan.
- Notification events on the Sprint 9 bus (seller and staff audiences, admin-editable rules): `product.approved`, `product.rejected`,
  `product.held` (seller: waiting for a person; staff: new item in the queue), `product.suspended`, `import.finished`.
  **How the old kinds map (checked):** the bus's `Target` carries the notification `kind`, and the return events already reuse the legacy
  kind names (`return_requested`, `return_decided` appear in both `defaults.py` and the event targets). So `product.approved` emits a seller
  `Target` with kind `product_approved` and the same params (`product`, `reason`) and link `("product", id)`: **the existing bell rendering and
  tabs do not change**, and the old `notify()` call is replaced by the event. Three templates are new (`product_held`, `product_suspended`,
  `staff_product_review_needed`), seeded by a migration in the way `0008_seed_sprint9_templates` did. Before the move, `product_reviewed()`
  stays as it is.

## G. API changes (all additive unless marked)

- **Seller:** `review` block on the product payload and `review_state` on list rows; `status=suspended` filter and tab key;
  `POST /seller/products/{id}/appeal`; `GET /seller/products/{id}/review-events` (timeline: submitted, checked, held, approved, edited).
  Edits on a `pending` product keep answering `409` but with `code: "under_review"` and now cover **every** edit endpoint (closing F1).
- **Admin:** `GET /admin/products` rows gain `review {state, decided_by, risk, reasons}` and filters `decided_by=system|staff`,
  `import_job`; `POST /admin/products/{id}/suspend`; reject accepts optional structured `codes`; approve and reject now record the actor.
  The queue's age and a "waiting more than N hours" count go to staff as a daily notice.
- **Frontend pack:** regenerated with the `review` contract and the polling rule; screens needed: submit confirmation with the
  under-review message, list badges and tab, product detail review panel with reasons and appeal, bulk report, admin queue with reasons.

## H. Phases (the flow first, because everything else plugs into it)

| Phase | Scope | Changes for users |
|---|---|---|
| 0 | **BUILT (see Progress).** Make the existing flow trustworthy: close F1 (edit locks on every endpoint), F2/F3 (`reviewed_hash` invariant, unarchive), F4 (actor and timestamps, notification inside the service), F5 (category counts on every transition), F6 (row lock), F12 (staff-only featured and new-arrival flags), F13 (currency follows the market), F14 (a duplicate starts unreviewed). Tests for each. **No AI.** | Staff and sellers see fewer surprises; `409 under_review` on more endpoints; no new statuses |
| 1 | **Flow engine with a manual decider:** `ProductCheck` queue, fast path + drain, `review` block and polling contract, `suspended`, bus events, `moderate_products` command, admin queue `review` fields. Every product ends in `waiting_for_person`, i.e. **identical to today for staff, but the seller sees "under review" and the full contract** | Frontend can build against the real contract before any model exists |
| 2 | Moderation phases 1-3 of the moderation plan plug in as the decider: rules, classifier, fit and novelty, shadow, assist, auto | `auto` per market, only after the pass criteria |
| 3 | **Bulk CSV import** on the same engine (CSV-1 to CSV-3 of the CSV plan) | Sellers upload; products arrive under review |
| 4 | Edit re-checks for live products, customer report, stats and drift, optional auto-reject | per market |

Phase 0 and 1 do not depend on any decision about models, regions or providers, and they fix real problems today. They are the right
first step regardless of how the AI part is decided.

## H2. Phase 1 in detail (the next build)

**Goal:** the seller sees "under review" with a stable contract, the work is a durable queue finished by a service, staff keep deciding by
hand. No model yet; the decider is pluggable so Phase 2 only supplies a better one.

1. **New app `moderation`** (small): `ProductCheck` (product, `trigger` submit|edit|unarchive|import|rescan|appeal, `content_hash` at the time,
   `status` queued|running|done|failed|stale, `attempts`, `locked_at`, `decision` approve|review|reject|error, `decided_by`
   system|staff, reasons JSON, timestamps) and `ReviewEvent` (the append-only timeline of a product: submitted, checked, held, approved,
   rejected, edited, withdrawn, suspended; actor and note).
2. **Engine** (`moderation/engine.py`): `enqueue(product, trigger)` (called inside `change_status(submit)` and `unarchive`, and by `note_edit` when
   `PRODUCT_EDIT_POLICY=check`, a third value that keeps the product live while the check runs); `claim()` (conditional update `queued → running`, stale
   `running` released after a timeout); `process(check)` calls the **decider**, then applies the result through the **existing**
   `portal.products.approve/reject` (so the trail, counts and notifications from Phase 0 are reused). A result is applied only if the product is still
   `pending` (or live and edited) **and** its fingerprint still equals the check's; otherwise the check is marked `stale` and discarded.
3. **Decider interface** (`moderation/deciders.py`): `decide(product) -> Decision(outcome, reasons, signals)`. Phase 1 ships `ManualDecider`
   (always `review`, so every product waits for staff exactly as today). Phase 2 adds the rules, classifier and similarity deciders behind the same interface.
4. **Running it:** `python manage.py moderate_products --watch --interval 2` and a `reviewer` service in `compose.yaml` (same image, `SKIP_MIGRATE=1`,
   no healthcheck, like `notifier`); `MODERATION_RUN=inline|worker` (inline = the request processes its own check after commit, for local development,
   tests and Preview); the product read endpoint processes a check that has waited more than a few seconds (a dead worker never leaves a seller waiting);
   attempts are limited, then `waiting_for_person` (fail closed).
5. **Contract** (additive): the `review` block (section C) on `SellerProductOut` and `review_state` on list rows; `GET /seller/products/{id}/review-events`;
   `POST /seller/products/{id}/appeal`; `Product.status` gains **`suspended`** with an admin `POST /admin/products/{id}/suspend` (the Figma admin screens ask for it), a
   `suspended` tab key in the seller KPIs and list filter, `suspended` hidden everywhere by the existing `public_products()` rule.
6. **Events on the bus:** `product.approved`, `product.rejected` (reusing the legacy kinds, so the bell does not change), `product.held`,
   `product.suspended`; staff `staff_product_review_needed` (market-scoped, permission `products.review`); templates seeded by a migration; the old
   `product_reviewed()` calls move to the bus in the same step.
7. **Admin:** `GET /admin/products` rows gain `review {state, decided_by, reasons}` and the filters `decided_by` and `import_job` (empty until CSV); the queue is
   ordered oldest-first with a "waiting more than N hours" count in the staff digest.
8. **Seed and docs:** the demo data gets one product in every state; `api-changes.md`, `data-model.md`, `environment.md`, the frontend pack regenerated.
9. **Tests:** enqueue on submit/unarchive/edit; claim is exclusive (two claimers, one winner); stale result discarded after an edit, a withdrawal and a
   second submit; dead-worker safety net; retries then fail-closed; the `review` block for every state and the poll sequence; `suspended` hidden from storefront,
   cart and checkout (an existing cart line turns unbuyable); events and bell kinds unchanged; query budgets for the polled product read and the admin queue.

**Needs from the owner before it starts:** K1 (is `pending` the "Under review" status), K3 (`flag`, `hold` or `check` for edits to live products; the
code defaults to `flag` until then), K4 (the hours promised for "waiting for a person", suggested 24).

## I. Tests that must exist

- Edit locks: every mutation endpoint on a `pending` product answers `409 under_review` (images, content, variants, options, delivery,
  duplicate-then-edit does not leak); parametrised over the router's operations so a new endpoint cannot forget it.
- Invariant: edit-then-unarchive, archive-edit-unarchive, edit a live product (changes hash → checked, hidden if flagged; stock edit → not checked).
- Stale result: edit or withdraw during a check → the result is discarded and the product is not approved; double submit → one check; two workers → one decision.
- Decision path: staff and system approvals both write actor and time, refresh counts, embed, log, notify; reject requires a reason; every
  path out of `active` refreshes counts.
- Fast path and drain: decided in the request when quick; left `queued` on timeout; finished by a poll; finished by the command; retries
  then fail closed to `waiting_for_person`.
- Contract: `review` block values for every state; the poll sequence `checking → active` and `checking → waiting_for_person`; tab counts;
  `suspended` hidden from `public_products`, cart and checkout (an existing cart line becomes not buyable).
- Bulk: N rows → N pending products and one summary notification; row failure does not stop the rest (CSV plan tests).
- Permissions and scope: staff see and decide only their markets; `products.review` required; a seller cannot read another store's check.
- Phase 0 findings: a seller cannot set `is_featured` or `is_new_arrival` (F12); `currency` cannot be changed away from the market's (F13); a
  duplicate of an approved or rejected product starts with no `reviewed_*` fields and no `import_key`, and is not submittable without
  pictures (F14); the content hash ignores stock, SKU, shipping and a price change inside the same band, and reacts to a new image, a
  changed tag, and a discount jump.
- Query budgets for the polled product endpoint (it will be called every 2-3 seconds) and the admin queue (Zeal N+1 check); OpenAPI names unique.

## J. Suggestions

1. **Do Phase 0 first, whatever happens with the AI.** F1-F3 are real ways around review today, and F5 gives wrong category counts.
2. **Keep `pending` as the one "Under review" status** and carry detail in `review.state`. The frontend's tabs keep working; only
   `suspended` is new.
3. **Make the stale-result rule non-negotiable** (hash + status checked under a lock when applying a decision). It is what makes
   background work safe against edits, withdrawals and double submits.
4. **Publish-first-review-after is possible later, only for a fast-track tier** (long-standing clean stores): their product goes live at once
   and is checked in the background, taken down if flagged. Not the default, and not before the shadow data exists. The owner's flow
   (not published until decided) is the right default.
5. **Tell the seller how long and what happens next**, in the market's language: the message and the hours are policy settings, not code.
6. **One bell per batch**, one e-mail per decision that needs action (held, rejected, suspended); approvals are a bell only. Staff get one
   daily digest of the queue and its age, plus an immediate notice if the queue is older than the promise made to sellers.
7. **Show the history.** A `review-events` timeline on the product answers "why is it not live?" for sellers and staff and cuts support load.
8. **Seed the demo data** with one product in every state (checking, waiting for a person, rejected with reasons, suspended) so the
   frontend can build every screen on Preview without waiting for the models.
9. **Measure the promise:** time from submit to decision (system and staff), share decided by the system, share overturned, queue age.
10. **Search on publish needs no work** (F9): a new product is found by name at once, and by tags and description within about 30
    seconds when the `worker` embeds it. If that delay ever matters, embed inside the decision.
11. **Decide who may set the "Featured" and "New arrival" flags** (F12) and make the currency follow the market (F13) in Phase 0; both are
    one-line schema changes with a test, and both are ways to look better than review allows.

## K. Decisions needed from the owner (my recommendation first)

1. **Is `pending` ("Under review") the status the seller sees while checking?** Recommend yes, with `review.state` for detail.
2. **Start with Phase 0 and Phase 1** (fix the gaps, build the flow with a manual decider) so the frontend gets the full contract now and
   the AI plugs in later? Recommend yes.
3. **Live products that are edited:** recommend a re-check inline and, only if flagged, back to `pending` (hidden until a person looks).
   The alternative, hiding the product during every re-check, would take shops offline for typo fixes.
4. **Promise to sellers:** what hours for "waiting for a person" (policy setting per market; suggest 24 h to start)?
5. The earlier open decisions still stand: may the system reject on its own (recommend no at launch), the 5-product probation, the photo
   policy, who writes policy content per market, the pass criteria for `auto`, and customer reviews.

## L0. Items from the earlier "not yet checked" list, now resolved (2026-10-27)

| Item | Result |
|---|---|
| Does keyword search find a product before its embedding exists? | **Partly, and enough.** By name, short description and brand at once (fallback in `hybrid_search`); by tags, description and benefits once the `worker` has embedded it (about 30 s). F9. |
| How do the legacy seller notification kinds map to the Sprint 9 bus? | **Cleanly.** Events emit `Target`s with a `kind`; return events already reuse the legacy kinds. `product.approved` and `product.rejected` keep their kinds and params, so the bell does not change. Section F. |
| Does `duplicate` copy anything it should not? | **Not today; it would once new columns exist.** It copies all product columns except a fixed list, and no images or delivery options. The list must be extended (F14). |
| The exact fields in the content hash | **Settled** in section B (with the price signature and what is deliberately left out). |
| Does the frontend tolerate an unknown status? | **Cannot be checked from here:** I may not read the frontend's code. What I can see is that the Figma admin Products screens already include a **Suspended** tab and a **suspend** action (`BACKEND_ASKS.md`). For the seller screens, the frontend session should confirm the two questions below. |
| Vercel function time limit and cron | **Dropped:** the first deployment is EC2 (section D). |

**Two questions for the frontend session** (the owner can paste them): (1) does the seller product list and the product detail screen
render a `status` value it does not know, and does it ignore an extra key in the tab counts, without breaking? (2) will it poll
`GET /seller/products/{id}` every 2-3 seconds while `review.state` is `checking`, as it does for the WhatsApp check?

## L. What I verified and what I did not

- Read in the code: `catalog/models.py` (statuses, indexes), `catalog/services.py` (`public_products`, stock refresh, category counts),
  `portal/products.py` (create, patch, options, variants, images, content, `change_status`, `completion`, `approve`, `reject`, `serialize`),
  `portal/api.py` (every product route and the admin queue), `orders/services.py` (`_variant_purchasable`), `discovery/indexing.py`,
  `notifications/services.py` and `defaults.py`, `portal/auth.py` (`StoreAuth`), the team activity log, the existing tests for submit
  and approve, `settings.py` for Vercel, `docs/environment.md` section 6, `docs/frontend-handoff.md`.
- Also read in the second pass: `discovery/search.py` (hybrid search and its fallback), `notifications/bus.py` and `catalog.py` (targets
  and kinds), `compose.yaml`, `deploy/nginx.conf`, `portal/schemas.py` (`ProductPatchIn`, `VariantPatchIn`), `core/uploads.py`
  (`safe_image`), `duplicate()`, and the frontend's `BACKEND_ASKS.md` (the only file of theirs I may read).
- Still open: whether the seller frontend tolerates an unknown status (the two questions above); the EC2 sizing (one instance for
  `web`, `db`, `redis`, `worker`, `notifier`, `reviewer`, `realtime` is the starting shape: to size once the models exist); and whether the
  frontend wants the `review` block named as in section C.

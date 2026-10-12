# Returns: customer return requests and admin-managed return policies (plan)

Status: **built on branch `returns` (2026-10-13), not pushed.** Planned against Figma snapshot 2026-10-12T13:16Z. See section 10 for what was built and where it differs from the plan.

## 1. Where we are

| Area | Today |
|---|---|
| Policy | `markets.ReturnPolicy` (1:1 country): `window_days`, `summary` (i18n), `url`, `is_active`. Edited in Django admin. The window is **snapshotted** on `Order.return_window_days`; the buyer order detail shows `return_eligible_until`. |
| Buyer | **Cannot request a return.** Only "cancel" before shipping exists. |
| Seller / staff | Can move delivered lines to `returned` (goodwill) or staff can set the order `returned`/`refunded`. No request, no reason, no approval, no evidence. |
| Money | `ledger.refund()` gives back the **whole** store sale and the **whole** commission, once per (order, store). It cannot refund one item. |
| Design | Seller and Admin sidebars have a **Returns** entry; **no Returns screen exists** and no buyer "Return item" screen. The only return text is "Easy Returns - 30 days" and Business Info "Returns Address". |

So the API can be built, but the buyer, seller and admin screens do not exist yet. They have to be requested from the designer (section 7).

## 2. Challenge: what to build

Asked: a customer return API plus admin-defined policies per region. A single window number per country is not enough:

* **The law differs by reason, not only by country.** For South Africa, a change-of-mind right for online buys is usually the ECTA 7-day cooling-off, and defective goods get a 6-month right under CPA s.56. For Pakistan I found no national statutory window, so sellers set 7-14 days. This is why the design must separate "change of mind" from "defective / not as described". (Secondary sources only. Needs legal confirmation. Not legal advice.)
* **Some goods are not returnable.** The store sells cosmetics (NARS BB Cream). Opened beauty items are normally excluded. So the policy needs per-category rules, like `CommissionRule`.
* **Partial returns break the ledger.** A buyer returning one of three items must not refund the whole store sale.

**Recommendation:** a real return request (RMA) with an approval step, rules as data, and per-return ledger entries. Skip the thinner option of "just add a Return button that flips status to returned": it skips the seller's say, takes no evidence, and refunds the wrong amount.

## 3. Policy model (admin-managed, any region)

All editable in Django admin now (inlines under `Country`), and through a staff API for the Admin portal Settings screen later.

1. **`ReturnPolicy`** (existing, one per country): add
   `change_of_mind_days` (0 = not accepted), `defect_days`, `starts_from` (delivery | ship), `restocking_fee_bps`,
   `seller_pays_return_when_at_fault`, `terms` (i18n long text). `window_days` stays and is read as the change-of-mind window.
2. **`ReturnReason`** (new): `code`, `label` (i18n), `kind` (change_of_mind | defect | wrong_item | not_as_described | other),
   `requires_photo`, `seller_fault`, `countries` (blank = all), `is_active`, `sort_order`. Seeded.
3. **`ReturnRule`** (new, same style as `CommissionRule`): country (nullable = all) + category (nearest parent wins) →
   `returnable`, `window_days` override. Cosmetics can be non-returnable once opened.
4. **Config registry** (Sprint 7 layer, scoped store, country, global): `returns.auto_approve` (false), `returns.seller_response_days` (3),
   `returns.max_photos` (5), `returns.allow_escalation` (true).
5. **Snapshot at purchase:** add `OrderItem.return_days` and `OrderItem.returnable`, copied at checkout, so later edits never change an existing order (same rule as today's order-level window).

Resolution order for one line: rule (category) > policy > none. Reason kind picks which window applies.

## 4. Return request flow

`ReturnRequest` (one per order per store, since each store fulfils and is paid separately) with `ReturnItem` lines.

```
requested -> approved -> received -> refunded
    |           |
    |           +-> (buyer or seller cancels before receipt) cancelled
    +-> rejected -> escalated (buyer disputes) -> staff approve | uphold rejection
    +-> cancelled (buyer withdraws while requested)
```

* **Eligibility** (computed, never trusted from the client): every line delivered; now <= delivered_at + window for that line and reason kind; line returnable; not already in an open or closed request.
* **Request:** items, reason, note, photos (required if the reason says so). Snapshot of the policy used is stored on the request.
* **Seller:** approve or reject (reject needs a reason shown to the buyer). If no reply within `seller_response_days` the request shows as **overdue** and appears in the staff queue (computed on read, so no background worker is needed, which matters on Vercel).
* **Buyer ships back:** enters a tracking number (no courier integration).
* **Seller marks received:** condition (good | damaged); restock yes/no. Good + restock writes a `StockMovement(reason=return)`; the existing `RETURN` reason is reused.
* **Refund:** posted when received (or by staff). Amount = returned lines (less their share of any voucher) minus restocking fee, plus delivery charge only when the reason is `seller_fault` and the whole store order comes back.
* **Existing goodwill paths** (seller sets `returned`, staff sets `returned`/`refunded`) keep working. If a request is open for those lines they are blocked, so money cannot move twice.

## 5. Ledger change (the risky part)

* `LedgerEntry.return_request` (nullable FK). Replace the single unique (order, store, type) for `refund`/`fee_refund` with two partial unique constraints: one for whole-order refunds (no request) and one per (order, store, type, return_request).
* `refund_items(request)`: refund = the amount above; **commission returned pro-rata** to the refunded share of the store's sale; idempotent per request.
* If the sale was already released to the seller's available balance, the refund comes off the balance and it can go negative. This already works for whole refunds. I would add a visible "negative balance" flag, not a new rule.
* Buyer-side refund to the card: **no PSP is integrated yet**, so the request ends as `refunded` with `payout_status = pending_psp` and staff complete it. Same limitation as today's order refunds.

## 6. API (all region-scoped via the existing `X-Region` middleware)

| Who | Endpoint | Purpose |
|---|---|---|
| Public | `GET /returns/policy` | Active policy, terms, reasons, window for the request's region (also folded into `/regions/current`) |
| Buyer | `GET /orders/{id}/returnable` | Per line: eligible?, deadline, why not, allowed reasons |
| Buyer | `POST /orders/{id}/returns` | Create a request |
| Buyer | `GET /returns`, `GET /returns/{id}` | Tabs + timeline |
| Buyer | `POST /returns/{id}/cancel`, `/ship` (tracking), `/escalate` | |
| Seller | `GET /seller/returns` (+ `/summary`, tabs), `GET /seller/returns/{id}` | Section `orders`, same permissions model as Orders |
| Seller | `POST .../approve`, `/reject`, `/received` | |
| Staff | `GET /admin/returns` (escalated, overdue, all), `POST .../decide` | |
| Staff | `GET/PUT /admin/regions/{code}/return-policy`, CRUD for reasons and rules | Admin portal Settings (Django admin works today) |

Notifications: seller gets `return_requested`, `return_cancelled`, `return_escalated` (new category `returns`, shown under the Orders section). Buyers have no notification inbox, so buyer events are emails (needs the email delivery from the OTP discussion).

## 7. Screens we need from the designer

Buyer: "Return item" entry on View Details, return form (items, reason, note, photos), return status and timeline, "Returns" tab on My Orders, shipping-back instructions.
Seller: Returns list (tabs and counts), return detail with approve and reject actions.
Admin: Returns queue and dispute decision, Settings > Return policy per region (window, reasons, category rules).
I would not start the UI-facing field names until these exist; the APIs above are the backend contract (design is a hypothesis).

## 8. Build steps (about one sprint, one commit each)

1. Models, migrations, seed (reasons, ZA and PK policy values marked as placeholders), Django admin.
2. Policy resolver and eligibility (`returns/policy.py`), snapshot at checkout, backfill existing orders.
3. `ReturnRequest` state machine and services, stock handling.
4. Ledger `refund_items`, constraints, reconciliation with analytics.
5. Buyer, seller and staff APIs, permissions, notifications, email.
6. Tests (state machine, eligibility edge cases, refund maths to the cent, double-refund protection, region scoping, permissions), query-budget pins, demo seed, docs (`data-model`, handoff, developer guide).

## 9. Open questions (answers change the build)

1. Whole lines only, or partial quantity? **Recommend whole lines for v1** (the line status, ledger and restock are all per line).
2. Should the seller approve every return, or auto-approve (e.g. defects with photos)? **Recommend seller approves**, staff arbitrates, auto-approve left as a switch.
3. Who arranges return shipping: buyer with a tracking number (recommended), or a platform label?
4. Replacement or exchange as a resolution, or refund only? **Recommend refund only for v1.**
5. Real values for ZA and PK (change-of-mind days, defect days, which categories are non-returnable). Needs a business and legal answer; seeds are placeholders.
6. Who pays a restocking fee, and is there one at all?
7. Is it acceptable that card refunds stay manual until a payment provider is integrated?

## 10. As built

Decisions taken (the recommendations in section 9, since the owner said to proceed): whole lines only; sellers approve, staff arbitrate;
the buyer ships it back with their own tracking number; refund only; placeholder policy numbers; no restocking fee until an admin sets one;
card refunds stay manual (staff mark them paid). The owner also asked to **leave room for the technical lead's courier API**.

**Built**

* `markets.ReturnPolicy` gains `defect_days`, `restocking_fee_bps`, `terms`. `/regions/current` and the new `GET /returns/policy` expose them.
* New app `returns`: `ReturnReason`, `ReturnRule`, `ReturnRequest`/`ReturnItem`/`ReturnPhoto`/`ReturnEvent`, `ReturnShipment`; `policy.py` (rule resolution
  and eligibility), `services.py` (the state machine), `api.py`, `admin.py`, `sync_return_tracking` command. Reasons and notification templates are seeded by migrations.
* `OrderItem.return_terms` is written at checkout. Orders from before keep working through the order's own window.
* Ledger: `LedgerEntry.return_request`, `refund_items()`, `refund()` now refunds only what has not gone back, `release()` no longer skips a partly refunded store.
* Goodwill returns (seller or staff) are blocked with `409 return_in_progress` while a customer's request for the same lines is open.
* Notifications: category `returns` (shown with the Orders section) and kinds `return_requested`, `return_cancelled`, `return_escalated`, `return_decided`.
* Settings: `returns.courier`, `returns.auto_approve`, `returns.seller_response_days`, `returns.max_photos`, `returns.allow_escalation`.
* Demo seed: three returns (waiting, on its way, refunded). Query budgets pinned for the list and detail endpoints.

**The courier slot** (`returns/couriers/`)

1. Write a module with a `Courier` subclass: `slug`, `book_return(request, pickup, dropoff) -> Booking` (tracking number, label URL, courier id), and as the
   courier allows `cancel`, `track` (polling) and `parse_webhook` (verify the signature, return `TrackingUpdate`s). Keep credentials in the environment.
2. `register(...)` it and add its import to `couriers.load()`.
3. An admin sets `returns.courier` to its slug (per market or globally). Approving a return then books a pickup and fills `ReturnShipment`; the buyer sees the label and tracking
   in `shipment`. Callbacks arrive at `POST /couriers/{slug}/webhook`; polling is `manage.py sync_return_tracking`.
4. A courier that fails never blocks an approval (the shipment falls back to `manual`, the error is kept in `raw`), and an unknown slug in the setting falls back to `manual`.

A delivered parcel does **not** refund by itself: the seller still inspects and marks it received. The same registry is meant to carry the seller's outbound shipments
(`book_shipment`) later; that is not built.

**Bug fixes after review (2026-10-13, `docs/bugfix-plan.md`)**: refunded orders are not returnable again and a buyer is never owed more than the
ledger refunded; sellers act only on new requests; every transition locks its row; courier calls and e-mails run after commit; webhooks never guess
a parcel; commission comes back at each line's own rate; the seller's order screens follow the lines still in play; a final change-of-mind refusal
still allows a defect claim; uploads are stored under server-chosen names and KYC documents only through signed links. Tested on SQLite and Postgres.

**Not built / unverified**

* Screens (Q90), real policy numbers (Q91), the courier integration itself (Q92), PSP refunds (Q93), buyer e-mail in other languages (Q95), exchanges (Q96).
* (Postgres: tested, 2026-10-13.)

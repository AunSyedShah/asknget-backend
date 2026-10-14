# Sprint 8 — Delivery fee rule, and the customer order screens of 2026-10-14

> **§1 (the delivery rule) is built; §2 (the order screens) is not started.** Originally a draft: Written 2026-10-14 after the designer's update (Figma version `2410119034151911988`, saved by
> Asma Choudhary). Open decisions are in §6; the numbered questions go to `docs/data-model.md` §10 (Q99 onward) once this is approved.

**Design status.** The Figma text API is rate-limited until about 2026-10-17 (429, Starter plan), so only **renders** (PNG) of four
frames were read: View Details, Order Cancel, Return Order, Track order. Not read yet: Leave Review, Live Track Order, and the admin
screens Returns & Refunds, Return Details, Applications, Application Details, Account Information, Recent Activity (§5).

## 1. Delivery fee rule (new requirement from the owner)

**Requirement, as understood.** Admin **and** seller can each set (a) an order amount above which delivery is **free**, and (b) the **fee**
charged below it. "Dynamic" means these two numbers are configurable, not computed from weight or distance.

**Rule.** Per seller, on that seller's items in the cart: `store_subtotal >= free_over` → delivery costs 0, otherwise `fee`.
Shipping is already per seller (sprint 6 §3) and stays the seller's income (sprint 7, Q63), so a waived fee is the seller's cost.

### 1.1 Settings (config registry, `config/definitions.py`)

| Key | Type | Scopes | Meaning |
|---|---|---|---|
| `shipping.free_over_cents` | int, default 0 | global, country, store | Free at or above this. **0 = no free-delivery threshold.** Market currency, minor units. |
| `shipping.fee_cents` | int, default 0 | global, country, store | Charged below the threshold. |
| `shipping.rule_locked` | bool, default false | global, country | When true, store values are ignored: the admin's numbers apply to every seller in that market. |

The most specific scope wins (store, then country, then global), as everywhere else in the registry. Editing in Django admin needs no new
code; sellers get an endpoint (§1.4).

### 1.2 How it meets the options sellers already have

`StoreDeliveryOption.price_cents` becomes **nullable**. `null` means "follow the rule" (this is the Standard delivery); a number is a
fixed price (Express R25 stays R25). Existing rows keep their numbers, so **nothing changes until someone sets the rule and leaves a
price blank.** Pickup keeps its own price.

### 1.3 Code

* `orders/shipping.py` (new): `price_for(option, store, country, store_subtotal) -> (price_cents, waived)`. It reads the three settings
  once per request (`config.get_many`) so it adds no query per store.
* `orders/services.py`: `cart_delivery_groups` computes each option's effective price (also for `_default_option`, which today uses
  `price_cents`); `cart_out` and `checkout` use it. The rule's input is the store's merchandise subtotal **before the voucher**: a voucher
  is cart-level, and splitting it across sellers is a design of its own (Q101).
* `cart_out.deliveries[]` gains `rule: {free_over_cents, fee_cents, store_subtotal_cents, remaining_cents, free}` so the cart can say
  "Add R120 more for free delivery".
* `orders.OrderDelivery` gains `waived` (bool) and `free_over_cents` (snapshot); `delivery_method_snapshot.per_store[]` gains `waived`.
  Old orders are never recalculated.
* No ledger change: the ledger posts what `OrderDelivery` says was charged.

### 1.4 Seller and admin surfaces

* **Seller** (`StoreAuth(section="products")`, next to the delivery options): `GET /seller/delivery/rule` returns the effective values,
  where each came from (`store` / `country` / `global`) and whether the admin locked them; `PUT` sets the store's own values; `DELETE`
  falls back to the platform default. `409 rule_locked` while locked. Delivery-option create and patch accept `price_cents: null`.
* **Admin:** Django admin on the config (global and per country), including the lock. An admin API only if the admin portal needs one (Q102).

### 1.5 Tests

Free exactly at the threshold, one cent below, 0 = never free; a fixed-price option is unaffected; store override, country override, lock;
several sellers in one cart each judged on their own items; the cart hint numbers; checkout snapshots `waived`; an old order is unchanged
after the rule changes; the seller endpoints and `rule_locked`; query budgets stay flat.

## 2. Customer order screens (from the renders of 2026-10-14)

| # | Screen | Gap in our API | Change |
|---|---|---|---|
| 2.1 | **Order Cancel** modal: 4 fixed reasons | `cancel` takes free text only | New `CancelReason` (code, i18n label, active, order; admin-managed like `ReturnReason`), seeded with the four; `GET /orders/cancel-reasons`; `cancel` accepts `reason_code` (free text kept for "other"); the order stores code and label. The design's "Fount better price" is a typo for the designer. |
| 2.2 | **View Details**: "Visa •••• 4242" | the order keeps only the payment type | `Order.payment_brand` and `payment_last4`, copied at checkout from the saved card; `payment_card` on the order detail. Card numbers are still never stored. |
| 2.3 | **View Details / Track order**: "Estimated delivery" | options carry only a free-text ETA | `StoreDeliveryOption.eta_min_days` / `eta_max_days` (optional); `OrderDelivery` snapshots them; order detail gets `estimated_delivery_until`. |
| 2.4 | **Track order**: tracking number, courier, "Live Tracking" | no forward-shipment courier data | `OrderDelivery.courier`, `tracking_number`, `tracking_url`, `eta_at`, all nullable and returned in the order detail now; the seller enters the number when marking the order shipped. **The technical lead's courier API fills these automatically later** (the same seam idea as returns). |
| 2.5 | **Return Order**: "Refunds within 5-7 business days" | not in the policy | `markets.ReturnPolicy.refund_days_min` / `refund_days_max`, returned by `/returns/policy`. |
| 2.6 | **Return Order**: "up to 3 photos" | `returns.max_photos` default is 5 | Default to 3, or tell the designer (decision 4). Everything else on the form is already covered: reason, comments, photo proof up to 5 MB, per-item request. |
| 2.7 | **Return Order**: refund method Original payment or **Wallet** | no wallet | **Not built** (Q103): store credit is a new money feature (customer balance, a liability, expiry, fraud). `refund_method` is accepted but only `original` is valid until decided. |

Also noted for the designer: prices appear as `$` and `R` on the same screen; the order has more statuses than the design's five steps
(pending payment, packed, returned, refunded), so the frontend maps them.

## 3. Order of work

1. §1 settings, migration (`price_cents` nullable, `OrderDelivery` fields), `orders/shipping.py`, cart and checkout, tests.
2. §1.4 seller endpoints and the option API, tests; admin docs.
3. §2.1, 2.2, 2.3, 2.5 (small, independent), then 2.4's seam and the seller's tracking-number entry, then 2.6.
4. Docs: `frontend-handoff.md` (cart `rule`, cancel reasons, tracking fields), `data-model.md`, `environment.md` if needed, the endpoint index.
5. Run the full suite on SQLite and Postgres, then push **`develop`** (Preview) only.

## 4. Safety and compatibility

All migrations are additive or relax a constraint (`price_cents` nullable); no data is rewritten. With no rule set, behavior is identical to
today. Nothing here touches production until the owner merges into `main`.

## 5. Not read yet / waiting

The Figma text API unlocks about 2026-10-17. Until then: renders of Leave Review, Live Track Order and the admin Returns & Refunds and
Return Details screens (these should match the returns feature already built). Courier forward tracking and Live Tracking wait for the
technical lead's API.

## 6. Decisions needed

1. **Rule basis:** the seller's items before the voucher (this draft), or the whole cart (then the platform, not the seller, pays)?
2. **Which options follow the rule:** only those with a blank price (this draft), or every delivery option?
3. **Admin lock** per country (this draft), or sellers always free to override?
4. **Photos:** default 3 (the design) or keep 5?
5. **Wallet refunds:** build later as its own sprint, or drop?

## 7. As built (2026-10-14): §1 the delivery rule

Built as drafted, with the draft's defaults (decisions 1-3 in §6 stay open and reversible). Differences and details:

* The three settings are in `config/definitions.py`; `orders/shipping.py` resolves them for every seller in the cart with one query (`rules_for`) and holds the seller-facing helpers.
* `StoreDeliveryOption.price_cents` is nullable (`sellers/0009`); `OrderDelivery` gained `waived` and `free_over_cents` (`orders/0009`). Nothing changes until someone sets the rule and blanks a price.
* The option API takes `price_cents: null` (not for pickup: `422`). The rule endpoints are `GET` / `PUT` / `DELETE /seller/delivery-options/rule`, with `409 rule_locked` while the admin has locked the market.
* The cart's `deliveries[]` carries `rule {free_over_cents, fee_cents, store_subtotal_cents, remaining_cents, free}`; options carry `waived` and `follows_rule`.
* 24 tests in `orders/test_delivery_rule.py`; the full suite is green (595). A flaky analytics test found on the way was fixed: search attribution now breaks timestamp ties by the event id.
* Questions Q99-Q102 are in `docs/data-model.md` §10.

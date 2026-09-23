# Sprint 6 — Close the design gaps (analytics, wizard fields, seller-defined shipping)

> Written before code, from `docs/design-sync-audit.md` (2026-09-24). Snapshot verified current.

Three areas the audit found out of sync. Two are additive; the third changes how shipping works.

Decisions taken with the user:
- **Q53 → fully seller-defined shipping.** Sellers create their own delivery options with names and prices;
  the platform `DeliveryMethod` stops driving checkout. I flagged that a multi-store cart then has no single
  set to choose from — §3 is how that is resolved.
- **Q49 → allocate order discounts pro-rata per store**, by share of subtotal.

## 1. Analytics — make the three screens real

### `GET /seller/analytics/overview` (Store Performance)
Adds, alongside the existing KPIs:
- `conversion_rate` KPI — currently only on the free dashboard.
- `daily` — a per-day sales series for the period ("Sales Performance").
- `sales_by_category` — `[{category, revenue_cents, share_pct}]` for the donut.
- `top_products` — rank, name, **image_url**, orders, revenue (the design's table).
- `customer_snapshot` — `{new, returning, one_time}`, the three counts
  `portal.customers.Customer.type()` already computes.

### `GET /seller/analytics/sales` (new — Sales Analytics)
- KPIs `gross_sales_cents`, `discounts_cents`, `refunds_cents`, `net_sales_cents`.
  **Net = gross − discounts − refunds − adjustments**, the designer's definition. The existing
  `/overview.net_sales_cents` (gross − cancelled/returned) keeps its meaning and is renamed in the docs to
  avoid two different "net"s.
- `revenue_over_time` — gross vs net per bucket, `?bucket=daily|weekly|monthly`.
- `revenue_breakdown` — the waterfall: gross → −discounts → −refunds → −adjustments → net.
- `sales_by_channel` — from a new `Order.channel` (`web` default, `mobile_app`, `other`), so the chart is
  honest rather than invented; everything reads *Website* until the clients start sending it.
- `sales_by_category` — category, orders, revenue, share.

Refunds and adjustments are **0 until the PSP exists** (Q50) and say so in the schema description — the
waterfall renders, the numbers do not lie.

### `GET /seller/analytics/products` (Product Analytics)
Adds `avg_product_revenue_cents` and `image_url` per row. `products_total`, `top` and `slow_movers`
already cover Total / Top Selling / Low Performing.

### Discount allocation
`orders.services.store_discount_cents(order, store)` — the store's share of `Order.discount_cents`,
pro-rata on its share of the order subtotal, largest-remainder rounded so the parts sum to the whole.
One helper, used by analytics now and payouts later.

## 2. Product wizard fields

| Field | Change |
|---|---|
| `Product.cost_price_cents` | new, nullable — the popup's "Cost Price"; feeds margin later |
| `Product.barcode` | new, blank — the popup's "Barcode" |
| `PRODUCT_IMAGE_MAX_BYTES` | 5 MB → **1 MB** ("PNG, JPG upto 1-MB") |
| `IMAGE_TYPES` | drop webp/gif → `{image/png, image/jpeg}` |

Both fields are seller-visible only; `cost_price_cents` must never reach a buyer-facing schema.

## 3. Seller-defined shipping (the model change)

### Models
| Model | Fields |
|---|---|
| `sellers.StoreDeliveryOption` | `store`, `name` ("Express Delivery"), `eta_label` ("1–3 business days"), `price_cents`, `kind` (`delivery` \| `pickup`), `is_active`, `sort_order`; unique `(store, name)` |
| `catalog.Product.delivery_options` | M2M → `StoreDeliveryOption`. **Empty means "all of the store's active options"**, so every existing product keeps working without a backfill |

`orders.DeliveryMethod` is **not dropped** (no destructive migration): it stops driving checkout, keeps its
rows, and is marked deprecated. `Cart.delivery_method` / `Order.delivery_method` columns stay, unwritten.

### How a multi-store cart chooses
This is the problem the chosen option creates, and the contract for it:

- Shipping is decided **per store**, not per cart. `cart_out` groups lines by store and returns, for each:
  the options every line in that group supports (intersection), which one is selected, and its price.
- Selection lives on the cart as `delivery_selections` — `{store_id: option_id}` — defaulting to the
  cheapest option for that store. `PATCH /cart/delivery {store_id, option_id}` changes one.
- `summary.shipping_cents` is the **sum across stores**; a cart with three sellers has three shipping lines.
- Checkout takes `deliveries: [{store_id, option_id}]` (optional — the cart's selections are used when
  omitted) and validates every store in the cart has a valid, supported option.
- `orders.OrderDelivery` snapshots the choice per store: `order`, `store`, `option` (nullable), `name`,
  `eta_label`, `price_cents`, `kind`. `Order.shipping_cents` remains the total, so existing order totals,
  invoices and exports are unaffected.

### Seller portal
`GET/POST/PATCH/DELETE /seller/delivery-options` — CRUD over the store's own options, and
`PUT /seller/products/{id}/delivery-options {option_ids}` for the wizard's Shipping step
(alongside the existing `handling_days`, which is the popup's "Processing Time").

### Existing data
A data migration gives every store the three options the design shows, priced from its market's current
platform `DeliveryMethod` rows where they exist (Standard → "Seller Delivery", Express → "Express
Delivery") plus "Customer Pickup". Nothing a buyer can currently do stops working.

## 4. Not in this sprint

Refund and adjustment amounts (Q50, needs the PSP), the Transactions page (Q52), category requests (Q55),
Returns / Notifications / Team-Managers (no screens), and the paused performance steps 2–6.

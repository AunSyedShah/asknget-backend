# Backend ↔ design sync audit — 2026-09-24

The Figma snapshot in `docs/figma/` is current (`snapshot.py --check` reports *unchanged* against the live
original). This is the answer to the next question: **is the backend current too?**

**Verdict: the backend matches the design everywhere it was built to, and is out of sync in four places** —
all of them things the designer drew *after* those sprints shipped. Nothing that was implemented has drifted;
the gaps are new work, not rework.

Design delta since the snapshot sprint 5 was built against (2026-09-21 → 2026-09-23):
571 insertions across 19 screens — three new Analytics screens, a Transactions screen, five product-wizard
popups, and a new "Transections" [sic] sidebar entry on every seller screen.

---

## 1. In sync — no action

| Area | Screens | Notes |
|---|---|---|
| Buyer storefront | Landing, Categories, PDP ×3, Cart, Checkout, Order Placed, Track Order, About, Contact | Unchanged in this delta |
| My Account | Personal Info, Orders ×4, Wishlist, Addresses, Payment Methods ×2, popups | Unchanged |
| Seller onboarding / KYC | Signup, OTP ×2, Basic Info, Business Info, Representative, Banking, Review & Submit, Application Submitted / Status / Approved / More Info / Update, Activate ×2 | Unchanged; country-aware KYC (sprint 4) still satisfies it |
| Seller portal | Login, Dashboard, Products ×2, Inventory, Orders ×2, View Order, Customers ×2, Customer Detail | Only change is the new sidebar entry (frontend-only) |
| Chat | Customer Detail → **Messages** tab | Confirms the sprint-5 model: one thread per (store, customer), opened from the customer record |

The sidebar still lists **Returns**, **Notifications** and **Team/Managers** with no screens behind them —
unchanged, still out of scope (Q20).

## 2. Out of sync

### 2.1 Analytics — three screens, we built a different set (biggest gap)

We ship six premium reports; the design draws three pages. They overlap but do not match.

| Design wants | Backend today |
|---|---|
| **Store Performance**: Total Sales · Total Orders · AOV | ✅ `/seller/analytics/overview` |
| …plus **Conversion Rate** | ⚠️ exists, but only on the *free* dashboard (`performance.conversion_rate`) — move or duplicate it into the premium overview |
| …**Sales Performance** daily line chart | ❌ no daily series in analytics (the free dashboard has a 12-month one) |
| …**Sales by Category** donut with % | ❌ |
| …**Top Products** (rank, image, orders, revenue) | ⚠️ `/analytics/products` has it bar the image |
| …**Customer Snapshot**: New / Returning / One-Time counts | ⚠️ `portal.customers.Customer.type()` already computes exactly these three; `/analytics/customers` just does not expose them as counts |
| **Sales Analytics**: Gross Sales | ✅ |
| …Net Sales | ⚠️ **same name, different number.** Design: gross − discounts − refunds − adjustments. Ours: gross − cancelled/returned lines |
| …**Discounts** | ❌ needs per-store voucher attribution — Q34/Q49, now blocking |
| …**Refunds** | ❌ no refund amounts exist until the PSP lands — Q50 |
| …**Adjustments** | ❌ undefined term — Q50 |
| …**Revenue Over Time** (gross vs net, Daily/Weekly/Monthly) | ❌ |
| …**Revenue Breakdown** waterfall | ❌ (follows from the four KPIs above) |
| …**Sales by Channel** (Website / Mobile App / Others) | ❌ no `Order.channel` — Q51 |
| …**Sales by Category** table | ❌ |
| **Product Analytics**: Total Products · Top Selling · Low Performing | ✅ `/analytics/products` (`products_total`, `top`, `slow_movers`) |
| …**Avg. Product Revenue** | ❌ trivial to add |

We also ship three reports the design does **not** draw — conversion **funnel**, **search terms**, and
**fulfilment timings**. They are genuinely useful and cost nothing to keep; flag them to the designer rather
than delete them.

### 2.2 Product wizard popups — never in any snapshot before

These five dialogs are drawn as top-level *groups*, which our snapshot tool silently skipped until this
sync. They specify the wizard we built in sprint 3.

| Popup | Backend |
|---|---|
| **Media** — drag & drop, "PNG, JPG upto 1-MB" | ⚠️ we allow **5 MB** and also webp/gif. Tighten to match, or confirm the copy is placeholder — Q54 |
| **Pricing** — Selling Price, Compare-at Price, Stock Quantity, Low Stock Threshold, variant matrix | ✅ all present |
| …**Cost Price** | ❌ **missing field** — needed for margin; see Q57 |
| …**Barcode** | ❌ **missing field** — Q57 |
| **Shipping** — Delivery Options: Express R25.00 / Seller Delivery R20.00 / Customer Pickup R15.00 | ❌ **model conflict.** `DeliveryMethod` is platform-level per market and chosen by the *buyer* at checkout; the design sets priced options per *product* — Q53 |
| …**Processing Time** "1–2 business days" | ✅ `Product.handling_days` |
| **Review & Publish** — product preview | ✅ preview of data `serialize()` already returns |
| **Category Request** — free text (≤250) + optional supporting document | ❌ **not implemented at all** — new models + staff queue — Q55 |

### 2.3 Transactions screen

Title, subtitle ("Track payments, payouts, fees and other financial activity for your store") and an Export
button; body not drawn. ❌ Not implemented — the payments sprint owns it (Q52).

### 2.4 Sidebar

Every seller screen gained a **Transactions** entry. Frontend-only; `GET /seller/plan` already drives which
entries are locked.

---

## 3. What I would do next

1. **Answer Q53 first** (per-product delivery options). It is the only gap that changes an existing model —
   everything else is additive. Until it is settled, `DeliveryMethod` should not be extended.
2. **Cheap wins, one small sprint**: conversion rate into the premium overview, `Avg. Product Revenue`, the
   New/Returning/One-Time counts, `Product.cost_price_cents` and `barcode`, and the 1 MB image limit. All
   additive, no open questions blocking them.
3. **Blocked on decisions**: discounts (Q49), refunds and adjustments (Q50), sales channel (Q51),
   transactions (Q52), category requests (Q55).
4. **Ask the designer** whether the funnel / search-terms / fulfilment reports we already have should get
   screens, since they are built and shipping.

Open questions live in `docs/data-model.md` §10 (Q49–Q57).

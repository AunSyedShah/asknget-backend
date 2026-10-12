# asknget — Backend Data Model (derived from Figma)

Source: Figma file `dAGMv3hUJXkyW4QmJPw20Q` ("asknget"), page **Web UI Design**, 38 screens, last modified 2026-09-11.
Status: **v0.1 draft** — derived purely from the design. Items marked ❓ are open questions for the team lead (collected in §10).

> **Design drift:** the Figma changes often (8 revisions on 2026-09-11 alone). Before planning or implementing against any screen, run `python docs/figma/snapshot.py --check`; if it reports a change, re-snapshot and `git diff docs/figma/` to see exactly which labels/fields/flows moved, then update this document. Treat the design as a hypothesis — flag inconsistencies rather than encoding them.

asknget is a **multi-vendor marketplace**: a buyer storefront (catalog → cart → checkout → orders) plus a **seller onboarding / KYC application flow** (5-step registration → review → approval → store activation). No admin or seller-dashboard screens exist in the design yet, so those are only sketched where the buyer/seller screens imply them.

---

## 1. Screen → entity map

| Screen(s) | Entities touched |
|---|---|
| Landing Page | Product, Category, Banner, NewsletterSubscriber |
| Categories (listing + filters) | Product, Category, Brand, ProductVariant (color), stock status, pagination/sort |
| Product Details (Desc / How to Use / Reviews) | Product, ProductContentSection, ProductOption/Variant, Review, ReviewMedia |
| Cart | Cart, CartItem, Voucher |
| Checkout, Order Placed | Order, OrderItem, Address (snapshot), DeliveryMethod, Payment, PaymentMethod |
| Track Order | Order (guest lookup by number + email), OrderStatusEvent |
| My Account – Personal Info | User (profile fields, verification flags, member-since) |
| My Account – My Orders (All/Ongoing/Completed/Cancelled) | Order, OrderItem (cancel, re-order, review) |
| My Account – Wishlist | WishlistItem |
| My Account – Manage Addresses, Add New Address popup | Address |
| My Account – Payment Methods, Add New Card popup | PaymentMethod |
| Password Manager | User (password change) |
| Logout popup | Session / refresh token |
| Contact Us | ContactMessage |
| Signup – Create Account, Email OTP, Mobile OTP | User (seller role), OtpVerification |
| Basic Info → Business Info → Representative → Banking → Review & Submit | SellerApplication (+ Directors, Documents, Categories) |
| Application Submitted / Status / More Info Required / Update Application | SellerApplication status machine, InfoRequest, ApplicationStatusEvent |
| Application Approved / Activate Seller Account / Account Active | Store |

---

## 2. Global conventions

- Primary keys: UUID. `created_at` / `updated_at` on every table (omitted below). Soft delete (`deleted_at`) on User, Product, Address, PaymentMethod, Review.
- Money: integer minor units (`*_cents`) + `currency` (ISO-4217). Design shows `$` only. **Resolved (sprint 4):** one currency per market — every store, product, cart and order belongs to a `markets.Country` whose `currency` it uses.
- Phone: E.164 string. Design shows `+92` (PK) and South African addresses (Johannesburg / Gauteng). **Resolved (sprint 4):** countries are admin-configured markets (`markets_country`); ZA and PK are seeded as examples — see `docs/sprint-4-plan.md`.
- Enums are written inline as `enum(...)`.
- Card numbers, CVV and full bank account numbers are **never stored raw**. Cards go through a PSP token; the bank account number is encrypted at rest and only `last4` is displayed ("Account Number: xxxx 1234").

---

## 3. Identity & access

### `users`
| field | type | notes |
|---|---|---|
| id | uuid PK | |
| email | citext unique | |
| phone | varchar unique, nullable | |
| password_hash | varchar | |
| full_name | varchar | Personal Info "Full Name" |
| first_name / last_name | varchar, nullable | checkout & seller flow use split names |
| gender | enum(male, female, other, prefer_not_to_say), nullable | Personal Info dropdown |
| date_of_birth | date, nullable | |
| profession | varchar, nullable | "Profession (Optional)" |
| country / city | varchar, nullable | dropdowns → ❓ free text vs lookup tables |
| address_line | varchar, nullable | single "Address" on profile (distinct from saved delivery addresses) |
| avatar_url | varchar, nullable | "Change Photo — PNG/JPG/GIF, max 2 MB" |
| email_verified_at | timestamp, nullable | "Email Verified: Verified" |
| phone_verified_at | timestamp, nullable | "Phone Verified: Verified" |
| status | enum(active, suspended, deleted) | "Account Status: Active" |
| last_login_at | timestamp, nullable | |

`created_at` is surfaced as "Member Since".

### `roles` / `user_roles`
`enum(customer, seller, admin)`; a user can hold several (a seller can also buy). The "Seller Portal" navbar implies a separately gated area.

### `otp_verifications`
| field | type | notes |
|---|---|---|
| id | uuid PK | |
| user_id | FK users, nullable | nullable to allow pre-account verification |
| channel | enum(email, sms) | Email OTP / Mobile OTP screens |
| target | varchar | the email/phone the code went to |
| purpose | enum(signup_email, signup_phone, password_reset, login) | |
| code_hash | varchar | 6-digit code, hashed |
| expires_at | timestamp | |
| attempts | smallint | lock out after N |
| resend_available_at | timestamp | design shows a 45 s cooldown |
| verified_at | timestamp, nullable | |

### `refresh_tokens`
`user_id`, `token_hash`, `device`, `ip`, `expires_at`, `revoked_at`. Logout popup → revoke current token.

### `terms_acceptances`
`user_id`, `document` enum(seller_terms, privacy_policy, seller_agreement, marketplace_policies), `version`, `accepted_at`, `ip`. Four separate checkbox agreements appear across signup / review / activation — keep an audit trail.

---

## 4. Buyer profile

### `addresses`
| field | type | notes |
|---|---|---|
| id | uuid PK | |
| user_id | FK users | |
| type | enum(home, office, other) | radio in popup |
| label | varchar, nullable | free label on cards: "Friend's House", "University" |
| full_name | varchar | required |
| phone | varchar | required |
| street_address | varchar | required |
| unit_floor | varchar, nullable | "Unit / Floor (optional)" |
| city | varchar | dropdown |
| province | varchar, nullable | appears in seller + update-application forms, not in the buyer popup ❓ |
| postal_code | varchar | required |
| country | char(2) | not in popup ❓ default from profile |
| delivery_instructions | text, nullable | "Call before delivery…" (marked * in design — ❓ really required?) |
| is_default | boolean | "Set as default address"; enforce one default per user |

### `payment_methods` (tokenised)
| field | type | notes |
|---|---|---|
| id | uuid PK | |
| user_id | FK users | |
| type | enum(card, paypal, apple_pay, google_pay, bank_transfer) | |
| card_brand | enum(visa, mastercard, other), nullable | radio "Card Type" |
| card_holder_name | varchar, nullable | |
| last4 | char(4), nullable | "VISA ....2389" |
| exp_month / exp_year | smallint, nullable | "Expires 08/26" |
| provider | varchar | e.g. stripe, paypal |
| provider_token | varchar | PSP token; PAN/CVV never persisted |
| is_default | boolean | "Set as default payment method" |

Design shows "Paypal ....2930 / Expires 08/26" — PayPal has no expiry; treat as a placeholder.

### `wishlist_items`
`user_id` FK, `product_id` FK, unique(user_id, product_id). Navbar shows a wishlist count.

---

## 5. Catalog

### `categories`
| field | type | notes |
|---|---|---|
| id | uuid PK | |
| parent_id | FK categories, nullable | design mixes top-level (Beauty) and sub (Makeup, Shoes, Crockery) → tree |
| name | varchar | |
| slug | varchar unique | |
| image_url | varchar, nullable | "Shop by Categories" tiles |
| sort_order | int | |
| is_active | boolean | |
| product_count | int (cached) | filter sidebar shows "Fashion — 128 items" |

Values seen: Fashion, Beauty, Electronics, Groceries/Food, Jewellery, Sports, Home & Dining / Home & Lifestyle, Accessories, Mobile Accessories, Digital Accessories, Crockery, Bags, Shoes, Makeup, Essentials, Other. ❓ canonical list — seller form and buyer filters disagree.

### `brands`
`id`, `name`, `slug`, `logo_url`, `is_active`. Filter sidebar: ZARA, NARS, Philips, Luminarc, Michael Kors, L'Oréal Paris, Samsung.

### `products`
| field | type | notes |
|---|---|---|
| id | uuid PK | |
| store_id | FK stores | seller ownership (see §7) |
| category_id | FK categories | card shows one category label |
| brand_id | FK brands, nullable | |
| name | varchar | |
| slug | varchar unique | |
| short_description | text | one-liner under the title on PDP |
| description_title | varchar, nullable | "Perfecting BB Cream" heading in Description tab |
| description | text (rich) | |
| price_cents | int | base price; variants may override |
| compare_at_price_cents | int, nullable | struck-through "$40.99" |
| cost_price_cents | int, nullable | wizard Pricing popup "Cost Price"; **seller-only**, never in a buyer schema (sprint 6) |
| barcode | varchar, blank | wizard Pricing popup "Barcode" (sprint 6) |
| currency | char(3) | |
| stock_status | enum(in_stock, out_of_stock, preorder) | derived from variant stock; "In Stock" badge, availability filter |
| rating_avg | numeric(2,1) cached | "4.9" |
| rating_count | int cached | "320 Reviews" |
| is_featured / is_best_seller / is_new_arrival | boolean | landing sections. ❓ manual flags vs computed |
| status | enum(draft, active, archived) | |
| published_at | timestamp, nullable | |

`discount_percent` ("40% Off" badge) is derived: `1 - price / compare_at_price`.

### `product_images`
`product_id`, `url`, `alt`, `sort_order`, `is_primary`. PDP has a main image + thumbnails.

### `product_content_sections`
For the "Key Benefits" list (Description tab) and the numbered "How to Use" steps.
`product_id`, `section` enum(benefit, usage_step), `title`, `body`, `sort_order`.

### `product_option_types` / `product_option_values`
Option types seen: **Size** (15 ml, 30 ml, 50 ml, 75 ml), **Shade** (Ivory, Natural, Caramel, Deep Honey), **Color** (Black, Orange, Gray; filter: Green, Cream, Black, Red, Yellow, Orange).
- `product_option_types`: `product_id`, `name`, `sort_order`
- `product_option_values`: `option_type_id`, `value`, `swatch_hex` nullable, `sort_order`

### `product_variants`
| field | type | notes |
|---|---|---|
| id | uuid PK | |
| product_id | FK products | |
| sku | varchar unique | |
| price_cents / compare_at_price_cents | int / nullable | |
| stock_qty | int | |
| is_active | boolean | |

`product_variant_option_values`: `variant_id`, `option_value_id` (one value per option type). Order lines show "Size: 30 ml | Shade: Natural" — built from this.

### `reviews`
| field | type | notes |
|---|---|---|
| id | uuid PK | |
| product_id | FK products | |
| user_id | FK users, nullable | form asks Name + Email, so guests may review ❓ |
| order_item_id | FK order_items, nullable | set → "Verified Purchase" badge; "Leave Review" on completed orders |
| reviewer_name | varchar | "Ayesha K." |
| reviewer_email | varchar | "will not be published" |
| rating | smallint 1–5 | |
| body | text | |
| status | enum(pending, approved, rejected) | moderation ❓ |

`review_media`: `review_id`, `url`, `type` enum(image, video). The 5★…1★ histogram is an aggregate query or cached counters on the product.

---

## 6. Cart, checkout, orders

### `carts` / `cart_items`
- `carts`: `id`, `user_id` nullable (guest carts by `session_token`), `order_note` text nullable ("Order Notes (Optional)"), `voucher_id` FK nullable, `expires_at`.
- `cart_items`: `cart_id`, `variant_id`, `qty`, unique(cart_id, variant_id). Navbar shows a cart count.

### `vouchers`
| field | type | notes |
|---|---|---|
| code | varchar unique | "Discount Voucher → Apply" |
| type | enum(percent, fixed) | design shows "Discount (20%)" |
| value | int | percent or cents |
| min_subtotal_cents | int, nullable | |
| max_discount_cents | int, nullable | |
| starts_at / ends_at | timestamp | |
| usage_limit / used_count | int | |
| per_user_limit | int, nullable | |
| is_active | boolean | |

`voucher_redemptions`: `voucher_id`, `user_id`, `order_id`.

### `delivery_methods` — **deprecated (sprint 6)**
`id`, `country_id`, `name`, `eta_min_days`, `eta_max_days`, `price_cents`, `is_active`. Was the platform's
per-market catalogue the buyer chose from at checkout — Standard (5–7 days), Express (2–3 days). Shipping is
now the seller's (§7d): the table and its rows are kept (no destructive migration, old orders still resolve
their snapshot) but nothing writes `Cart.delivery_method` / `Order.delivery_method` any more.

### `orders`
| field | type | notes |
|---|---|---|
| id | uuid PK | |
| order_number | varchar unique | "ORD-00123" — sequence-based, human-readable |
| user_id | FK users, nullable | guest checkout ❓ (Track Order accepts an email → implies guests) |
| status | enum(pending_payment, confirmed, processing, shipped, out_for_delivery, delivered, cancelled, refunded) | UI groups: **Ongoing** = pending…out_for_delivery, **Completed** = delivered, **Cancelled** = cancelled/refunded |
| contact_first_name / contact_last_name / contact_email / contact_phone | varchar | "Contact Info" block |
| shipping_address | jsonb snapshot | street, city, postal_code, country… |
| billing_address | jsonb snapshot, nullable | "Ship to a different address" toggle ⇒ billing may differ |
| delivery_method_id | FK, nullable | deprecated, unwritten since sprint 6 — see `orders_orderdelivery` |
| delivery_method_snapshot | jsonb | name + ETA at time of order; superseded by `orders_orderdelivery` |
| channel | enum(web, mobile_app, other) | how the order was placed, sent by the client at checkout; default `web` (sprint 6, Q51) |
| special_note | text, nullable | "Special Note (Optional)" + cart order note |
| subtotal_cents | int | |
| shipping_cents | int | "$10.99" |
| discount_cents | int | "-$20.00" |
| voucher_code | varchar, nullable | snapshot |
| total_cents | int | |
| currency | char(3) | |
| payment_method_type | enum(card, bank_transfer, paypal, google_pay, apple_pay) | radio at checkout |
| payment_status | enum(unpaid, authorized, paid, failed, refunded, partially_refunded) | |
| placed_at | timestamp | |
| cancelled_at / cancel_reason / cancelled_by | nullable | buyer "Cancel" on ongoing orders ❓ allowed until which status |

### `order_items`
| field | type | notes |
|---|---|---|
| id | uuid PK | |
| order_id | FK orders | |
| store_id | FK stores | multi-vendor: needed for seller payouts/fulfilment |
| product_id / variant_id | FK | |
| product_name | varchar snapshot | |
| variant_summary | varchar snapshot | "Size: 30 ml \| Shade: Natural" |
| image_url | varchar snapshot | |
| qty | int | |
| unit_price_cents | int | "$25.99" |
| compare_at_price_cents | int, nullable | "$40.99" struck |
| line_total_cents | int | |
| status | enum mirrors order status | per-item status for split shipments ❓ |

### `order_status_events`
`order_id`, `status`, `note`, `location` nullable, `actor` (system/seller/admin), `created_at`. Feeds the "Track Order" timeline and "we will keep you updated as it moves through preparation and delivery".

### `payments`
`order_id`, `provider`, `provider_payment_id`, `method_type`, `amount_cents`, `status`, `raw_response` jsonb, `paid_at`. Checkout offers "Save card for future checkout" → on success create a `payment_methods` row from the PSP token.

Re-Order (cancelled orders) is a server action that copies items into the current cart; no table.

---

## 7. Seller onboarding & stores

### `seller_applications`
One row per seller user; edited step by step (draft autosave — "Save & Continue"), then submitted.

| field | type | notes |
|---|---|---|
| id | uuid PK | |
| user_id | FK users, unique | |
| application_number | varchar unique | "ASK-SLR-004821" |
| account_type | enum(individual, sole_proprietor, registered_company) | signup radio |
| status | enum(draft, submitted, under_review, more_info_required, resubmitted, approved, rejected, active) | state machine below |
| current_step | smallint 1–5 | stepper |
| **Step 1 – Basic info** | | |
| first_name / last_name | varchar | |
| date_of_birth | date | |
| id_passport_number | varchar (encrypted) | |
| phone / email | varchar | |
| residential_address / residential_postal_code / residential_city / residential_province | varchar | |
| **Step 2 – Business info** | | |
| legal_business_name | varchar | |
| store_display_name | varchar | |
| business_type | enum ❓ options not in design | dropdown "Select business type" |
| company_registration_number | varchar | appears twice in the design (duplicate) |
| vat_number | varchar, nullable | "(Optional)" |
| business_address / business_city / business_province / business_postal_code | varchar | "Update Application" edits these separately |
| pickup_address / pickup_same_as_business | varchar / boolean | |
| returns_address / returns_same_as_business | varchar / boolean | |
| store_description | varchar(250) | "0 / 250 characters" |
| **Step 3 – Representative** | | |
| rep_first_name / rep_last_name | varchar | |
| rep_role | enum ❓ (director, owner, manager, …) | "Select role"; review shows "Director" |
| rep_id_passport_number | varchar (encrypted) | |
| rep_mobile / rep_email | varchar | |
| **Step 4 – Banking** | | |
| bank_id | FK banks | dropdown; "Meezan Bank" |
| bank_account_type | enum(business, personal, savings, cheque) ❓ | "Business Account" |
| account_holder_name | varchar | |
| account_number_encrypted / account_number_last4 | bytea / char(4) | |
| branch_code | varchar | |
| tax_reference_number | varchar, nullable | "(optional)" |
| **Step 5 – Review** | | |
| info_accuracy_confirmed_at | timestamp | checkbox 1 |
| terms_accepted_at | timestamp | checkbox 2 |
| **Workflow** | | |
| submitted_at / reviewed_at / approved_at / rejected_at / activated_at | timestamp, nullable | |
| reviewer_id | FK users (admin), nullable | |
| reviewer_message | text, nullable | "More Info Required" banner |
| rejection_reason | text, nullable | |

Steps 1 and 3 overlap heavily for `individual` accounts — the representative section can be prefilled or hidden for that type. ❓ confirm which steps apply per account type (individuals likely skip the company registration doc).

**Status machine**
`draft → submitted → under_review → (approved | more_info_required | rejected)`
`more_info_required → resubmitted → under_review`
`approved → active` (on "Activate & Start Selling")
The Application Status screen shows the visible stages: Submitted → Under Review → More Info → Approved → Active.

### `seller_application_categories`
`application_id`, `category_id` — multi-select "Product Categories" (Fashion, Beauty, Electronics, Groceries, Jewellery, Sports, Home & Dining, Accessories, Mobile Accessories, Other).

### `seller_application_directors`
"Additional Directors (if any) → + Add Another Director": `application_id`, `first_name`, `last_name`, `role`, `id_passport_number` (encrypted), `email`, `phone`, `sort_order`.

### `seller_documents`
| field | type | notes |
|---|---|---|
| id | uuid PK | |
| application_id | FK | |
| type | enum(id_passport, company_registration, bank_confirmation, proof_of_address) | the four docs on the Review screen |
| file_url / file_name / mime_type / size_bytes | | "PDF, JPG or PNG · Max 10 MB" |
| version | smallint | replacements keep history ("Current Document: company-registration.pdf" → "Replace Document") |
| status | enum(uploaded, accepted, rejected, replacement_requested) | |
| reviewer_note | text, nullable | |
| superseded_by_id | FK self, nullable | |

### `seller_info_requests` / `seller_info_request_items`
"More Info Required" lists numbered required updates, each targeting a document or a field group:
- `seller_info_requests`: `application_id`, `reviewer_id`, `message`, `created_at`, `resolved_at`
- `seller_info_request_items`: `request_id`, `kind` enum(document, field_group), `target` (e.g. `company_registration`, `business_address`), `instruction`, `resolved_at`

### `seller_application_status_events`
`application_id`, `from_status`, `to_status`, `actor_id`, `note`, `created_at` — drives the "Application Progress" timeline.

### `stores`
Created on approval, activated on "Activate & Start Selling".
| field | type | notes |
|---|---|---|
| id | uuid PK | |
| owner_user_id | FK users | |
| application_id | FK seller_applications | |
| display_name | varchar | "Store Display Name" |
| slug | varchar unique | "asknget.com/store/huda-beauty" |
| description | varchar(250) | |
| logo_url / banner_url | nullable | "Complete Profile — add your store information" |
| status | enum(pending_activation, active, suspended, closed) | |
| seller_agreement_accepted_at | timestamp | activation checkbox 1 |
| info_reconfirmed_at | timestamp | activation checkbox 2 |
| activated_at | timestamp, nullable | |

The post-activation "Quick Start" (Add Product, Setup Delivery, Complete Profile) implies a seller dashboard that is **not in this Figma** — seller product CRUD, inventory and `store_delivery_settings` are out of scope for v0.1, but `products.store_id` and `order_items.store_id` are placed now so it can be added without painful migrations.

### `banks` (lookup)
`id`, `name`, `country`, `swift`, `is_active` — populates "Select your bank".

---

## 7b. Markets (sprint 4 — team-lead re-scope, not in the Figma)

A country is a separate market. Staff maintain all of this in Django admin; the API resolves the
market per request (`X-Region` / `?region=` → host subdomain `za.asknget.com` → default) and activates
its language and timezone. Full contract: `docs/sprint-4-plan.md`.

### `markets_country`
| field | type | notes |
|---|---|---|
| code | char(2) PK | ISO 3166-1 alpha-2 |
| name, subdomain (unique) | varchar | `za` → za.asknget.com |
| currency | char(3) | one per market |
| timezone | varchar | IANA; activated per request |
| default_language, languages | varchar, json list | subset of `settings.LANGUAGES` |
| phone_prefix, region_label, postal_code_regex | varchar | address-form hints |
| is_active, is_default, sort_order | | exactly one default |

### `markets_kycpolicy` (1:1 country)
`mode` enum(manual — `provider` reserved), `required_documents` json `{account_type: [doc types]}` (empty =
platform defaults in `sellers/rules.py`), `document_labels` i18n json per type, `id_number_label`,
`id_number_regex`, `tax_id_label`, `company_registration_label`, `instructions` i18n json.

### `markets_returnpolicy` (1:1 country)
`window_days` (change of mind; 30), `defect_days` (defective / wrong / not as described; 30), `restocking_fee_bps` (change of mind only; 0), `summary` and `terms` i18n json, `url`, `is_active` (off = no returns at all in that market). The change-of-mind window is snapshotted onto `orders.return_window_days`; all three numbers are snapshotted per line (see 7f).

### Country on existing tables
`seller_applications.country` (from the sign-up region), `stores.country` (copied on approval, immutable),
`products.country` (denormalised; partial live index), `categories.product_counts` json per market,
`orders.country` + `return_window_days`, `delivery_methods.country` (name unique per country),
`vouchers.country` / `banners.country` nullable (= every market), `users.language`,
`addresses.country` (blank = the market), `seller_documents.type` + `tax_registration`,
`business_licence`, `other`.

---

## 7c. Seller plans and customer chat (sprint 5 — team-lead request, Chat/Analytics screens not yet readable)

### Plan
`stores.is_premium` (bool) is the whole plan model for now: staff toggle it in Django admin
(`LogEntry` records who and when). `portal/entitlements.py` maps a plan name to features —
`analytics_advanced`, `chat_inbox` — and `require()` raises `403 premium_required`.
Subscriptions, prices and self-serve upgrade wait for the payments sprint (Q42).

### `chat_conversation`
One thread per `(store, customer)` — unique together. `order` FK nullable (context when opened from an
order), `subject`, `last_message_at`, `last_message_preview`, `seller_unread`, `customer_unread`,
`seller_last_read_at`, `customer_last_read_at`, `closed_at`. Counters are denormalised and written only by
`chat.services.send()` / `mark_read()`, so the inbox costs one query.

### `chat_message`
`conversation` FK, `sender_role` enum(customer, seller, system), `sender` FK users nullable, `body`,
`flagged_at` / `flag_reason` (off-platform contact details — flagged for staff, never blocked).
Append-only; clients poll `?after=<message id>`.

Access: buyers must be signed in and may write to any active store **in their market**; reading and
replying is premium, except `GET /seller/chat/summary` (counts only) which every plan may call.

---

## 7d. Seller-defined shipping and analytics (sprint 6 — Figma 2026-09-23)

The design's product wizard prices delivery per product, which the platform-wide `delivery_methods` could not
express. Shipping now belongs to the seller (Q53); `docs/sprint-6-plan.md` §3 has the reasoning.

### `sellers_storedeliveryoption`
| field | type | notes |
|---|---|---|
| store_id | FK stores | `related_name="delivery_options"` |
| name | varchar(60) | the seller's own label, "Express Delivery"; unique per store |
| eta_label | varchar(60), blank | free text, "1-3 business days" — not two day-count columns, because sellers word this themselves |
| price_cents | int | in the store's market currency; 0 allowed (free delivery) |
| kind | enum(delivery, pickup) | pickup is never auto-selected for a buyer |
| is_active | boolean | an option used by an order is deactivated, never deleted |
| sort_order | smallint | |

Every store is created with the three the design shows (Express / Seller Delivery / Customer Pickup) via
`sellers.services.ensure_default_delivery_options()`, and a data migration gave existing stores the same,
priced from their market's `delivery_methods` rows where those existed. A store cannot end up with zero
active options — deleting the last one is a 409.

### `catalog_product_delivery_options` (M2M)
`product_id`, `storedeliveryoption_id`. **Empty means all of the store's active options**, so no product
needed a backfill and a seller adding an option does not have to revisit every listing.

### `orders_orderdelivery`
| field | type | notes |
|---|---|---|
| order_id | FK orders | `related_name="deliveries"` |
| store_id | FK stores | unique together with `order` — one shipping line per seller |
| option_id | FK storedeliveryoption, nullable | SET_NULL; the snapshot below is what matters |
| name / eta_label / kind / price_cents | snapshot | so later edits or deactivation never rewrite history |

`Order.shipping_cents` stays the **total** across stores, so existing totals, invoices and exports are
untouched. `carts.delivery_selections` is a JSON `{store_id: option_id}` map, defaulting per store to the
cheapest **delivery** option — not simply the cheapest, since pickup is usually free and silently making a
buyer collect in person would be wrong. A cart whose lines for one store share no common option cannot check
out (409), and says so.

### Analytics
No new tables: `GET /seller/analytics/sales` is derived from `order_items`, `orders` and
`order_status_events`. Two definitions of "net" coexist deliberately — `/overview.net_sales_cents` is gross
minus cancelled/returned, while `/sales.net_sales_cents` is the designer's gross minus discounts, refunds and
adjustments. Refunds and adjustments are **0 until the PSP exists** (Q50) and the schema says so.
`orders.services.store_discount_cents()` allocates an order's voucher across its stores pro-rata on subtotal
share, largest-remainder rounded so the parts sum to the whole (Q49); analytics uses it now, payouts later.

## 7e. Configuration, team, transactions, notifications (sprint 7 — Figma 2026-09-29, `docs/sprint-7-plan.md`)

The owner's principle: **the application should be as dynamic as possible.** Anything a business person
might change is data an admin edits in Django admin, not a constant.

### `config_setting` (typed scalar tunables)
`key`, `scope` (`global` \| `country` \| `store`), `country`, `store`, `value` JSON, `note`, `updated_by`. Resolved
store → market → global → code default; **no cache**, an edit applies on the next request. The catalogue of keys
lives in code (`config/definitions.py`: type, bounds, choices, allowed scopes, default) and every save is
validated against it, in admin or anywhere else; the database constraints back that up.

| Key | Default | Scopes | Used by |
|---|---|---|---|
| `payouts.hold_mode` | `return_window` (or `fixed_days`, `none`) | global, market, store | when a delivered sale becomes available |
| `payouts.hold_days` | 7 | global, market, store | `fixed_days` mode |
| `payouts.minimum_cents` | 0 | global, market | the smallest payout staff can create |
| `analytics.low_performer_min_views` | 20 | global, market, store | Product Analytics |
| `analytics.low_performer_limit` | 5 | global, market | length of that list |
| `team.invite_expiry_days` | 7 | global, market | invite links |
| `team.max_members` | 0 = unlimited | global, market, store | seat limit |
| `notifications.recent_days` | 90 | global | how far back the bell reaches |

### `config_commissionrule`
`scope` (`global` \| `country` \| `category` \| `store`) + the matching FK, `rate_bps` (500 = 5 %), `effective_from`,
`is_active`, `note`. Per order line the most specific rule already in effect wins: store → the line's category (the
nearest parent with a rule counts) → market → global; ties go to the latest `effective_from`. A future date
schedules a change. No rule at all means 0 and says so (`rule_id` null). One global 5 % rule is seeded as a
starting point.

### Team: `team_teamrole`, `team_storemember`, `team_storeactivity`
- **Sections** (the design's eight): `dashboard, orders, products, inventory, customers, marketing, analytics,
  transactions`; **levels** `none` < `view` < `manage`. Reading needs `view`, anything that changes state needs `manage`.
- `TeamRole` (key, name, `permissions` {section: level}, `counts_as_manager`, `is_active`): presets as data. Choosing one
  *copies* its levels onto the member, so editing a role never widens anyone already in it.
- `StoreMember` (store, user — null until accepted, email, role, `permissions`, status `invited` \| `active` \|
  `suspended`, `invite_token_hash` — sha256, the raw token exists only in the e-mail, `invite_expires_at`, `joined_at`,
  `last_active_at`). Unique per (store, lower(email)).
- The **owner is implicit** (`Store.owner`): all access, can't be edited or removed, the only one who manages the team.
- `StoreActivity` (store, actor, `action`, `summary`, target): an append-only history in words ("Updated price for
  BB Cream"). Autosave is deliberately not logged.
- Mapping of existing areas to sections: chat inbox → `customers`, delivery options → `products`, header search →
  whichever of products / orders / customers the person can view.

### Ledger: `ledger_ledgerentry`, `ledger_payout`, `ledger_ledgercounter`
Append-only; every balance is computed from it. `LedgerEntry`: store, `type`, signed `amount_cents`, currency,
`status`, order, payout, `reference` (`TXN-YYYYMMDD-<n>`, gap-free from a locked counter), `available_at`, `meta`.

| Type | Sign | Meaning |
|---|---|---|
| `sale` | + | items − the store's pro-rata voucher share + its own delivery charge. The stores' sales add up to the buyer's total to the cent |
| `fee` | − | commission, per line from the rule in force, scaled by the voucher; the rate and rule used are recorded in `meta` |
| `refund` / `fee_refund` | − / + | the sale back to the buyer / the commission back to the store, once per store per order |
| `adjustment` | ± | staff, always with a note |
| `payout` | − | money sent to the store (staff create and complete it) |

Rules enforced by the database: one `sale`/`fee`/`refund`/`fee_refund` per (order, store); the sign of each type.
`available_at` is empty until the store's goods are **delivered**, then set from the resolved `payouts.hold_mode`
(`return_window`: delivery + the order's return window; `fixed_days`; `none`: available at payment). A refund of money
not yet available shares the sale's date, so the two cancel out of the *held* amount instead of driving the balance
negative. Balances: **available** = Σ entries whose `available_at` has passed (failed payouts excluded); **held** =
Σ the rest; **pending payout** = Σ payouts still processing.

### Notifications: `notifications_notificationtemplate`, `_storenotification`, `_notificationread`
The event and its `params` are stored, **not the sentence**. `NotificationTemplate` (kind, category, `title` and `body` as
per-language JSON with English required, `is_active`) is rendered when read, in the reader's language, so one event
reads correctly to an English owner and an Urdu manager. An admin rewords, translates or switches a kind off. Read state
is per person (`NotificationRead`). A person sees a category only if they can view its section (orders → Orders,
payments → Transactions, inventory → Inventory, customers → Customers; `system` is for everyone).

---

## 7f. Returns (docs/returns-plan.md)

Rules are data, edited by staff per market (Django admin, or `/admin/returns/{policies,reasons,rules}`); what applied is
**snapshotted** so editing a rule never rewrites an order or a request.

### `returns_returnreason`
`code` (unique slug), `label` i18n json, `kind` (change_of_mind | defect | wrong_item | not_as_described | other),
`requires_photo`, `countries` (M2M; empty = every market), `is_active`, `sort_order`. The kind picks the window (`other` follows
change of mind) and who bears the cost: defect, wrong item and not-as-described are the seller's fault, change of mind is the buyer's.

### `returns_returnrule`
`country` (null = every market), `category`, `change_of_mind_days` and `defect_days` (null = follow the market, **0 = not accepted**),
`note`, `is_active`; unique (country, category). The nearest category (itself, then its parents) with a rule wins; within one category
the market's own rule beats the all-markets one. Typical use: opened cosmetics not returnable for change of mind, still returnable if defective.

### `orders_orderitem.return_terms`
JSON `{change_of_mind_days, defect_days, restocking_fee_bps}` written at checkout from the policy and rules. Empty on orders placed before
returns existed: those fall back to the order's own `return_window_days` for both windows, no fee.

### `returns_returnrequest` (+ `_returnitem`, `_returnphoto`, `_returnevent`)
One request is for **one store's** lines of one order (each store fulfils, is paid and refunds separately); whole lines only.
`reference` (RET-00123-1), `status` (requested, approved, rejected, escalated, received, refunded, cancelled), `is_final`,
`reason` + `reason_kind` snapshot, `note`, `seller_note`, `staff_note`, `terms` (deadlines and fee that applied), `response_due_at`
(overdue is computed on read, no worker), money: `items_cents`, `discount_cents` (the voucher share), `restocking_fee_cents`,
`delivery_refund_cents`, `refund_cents`, `refund_status` (none | pending | completed: pending = owed to the buyer, staff pay it until a PSP exists).
`received_condition`, `restocked`, timestamps, `decided_by`. `ReturnEvent` is the timeline (actor buyer | seller | staff | system).

### `returns_returnshipment` - the courier seam
`courier` (slug of the integration; `manual` = the buyer posts it), `status` (pending, booked, in_transit, delivered, failed, cancelled),
`tracking_number`, `tracking_url`, `label_url`, `external_id` (the courier's booking id), `pickup_address` / `dropoff_address` snapshots,
`raw` (last courier response or webhook), `booked_at`, `delivered_at`. `returns/couriers/` holds the interface (`Courier.book_return / cancel /
track / parse_webhook`), a registry, and the `manual` courier. Which one a market uses is the admin setting `returns.courier`.

### Ledger
`ledger_ledgerentry.return_request` (nullable FK). One refund and one commission refund per return (partial unique constraint);
whole-order refunds still post once per (order, store). `ledger.refund()` now gives back only what has not already gone back, and a store's
sale is released after a partial return as before.

### Settings (config registry)
`returns.courier` (manual), `returns.auto_approve` (false), `returns.seller_response_days` (3), `returns.max_photos` (5), `returns.allow_escalation` (true).

---

## 8. Marketing & support

### `banners`
Hero + "Top Discounted" tiles: `placement` enum(hero, top_discount, category_strip), `eyebrow` ("TECH ESSENTIALS"), `title`, `subtitle`, `badge_text` ("UP TO 50% OFF"), `cta_label`, `cta_url`, `image_url`, `sort_order`, `starts_at` / `ends_at`, `is_active`.

### `newsletter_subscribers`
`email` unique, `subscribed_at`, `unsubscribed_at`, `source`.

### `contact_messages`
`first_name`, `last_name`, `email`, `phone`, `subject` enum ❓ (options not in design — e.g. order_issue, returns, seller_support, general), `message`, `status` enum(new, in_progress, closed), `user_id` nullable, `application_id` nullable ("Contact Seller Support" on the application screens can reuse this).

### `site_settings`
Footer/contact info, "We Accept" logos, delivery/return policy blurbs — key/value or a single JSON row.

---

## 9. API surface implied by the screens

REST-style JSON; auth via access + refresh JWT.

**Auth**
- `POST /auth/register` (buyer) · `POST /auth/seller/register` `{account_type, phone, email, password, terms}`
- `POST /auth/otp/send` `{channel, purpose}` · `POST /auth/otp/verify` `{channel, code}` (45 s resend cooldown)
- `POST /auth/login` · `POST /auth/refresh` · `POST /auth/logout`
- `POST /auth/password/change` `{current, new}` · `POST /auth/password/forgot` / `reset` (no screen, but expected)

**Profile**
- `GET/PATCH /me` · `POST /me/avatar`
- `GET/POST /me/addresses` · `PATCH/DELETE /me/addresses/:id` · `POST /me/addresses/:id/default`
- `GET/POST /me/payment-methods` (accepts PSP token) · `DELETE …/:id` · `POST …/:id/default`
- `GET /me/wishlist` · `PUT/DELETE /me/wishlist/:productId`

**Catalog**
- `GET /categories` (tree, with counts) · `GET /brands`
- `GET /products?category=&brand[]=&color[]=&price_min=&price_max=&in_stock=&q=&sort=new|price_asc|price_desc|rating&page=&per_page=12` → `{items, total, page}` ("Showing 1–12 of 120")
- `GET /products/featured|best-sellers|new-arrivals` · `GET /products/:slug` (options, variants, content sections, rating summary) · `GET /products/:id/related`
- `GET /products/:id/reviews?sort=new|top&page=` · `POST /products/:id/reviews` (multipart, media optional)

**Cart & checkout**
- `GET /cart` · `POST /cart/items` · `PATCH/DELETE /cart/items/:id` · `PATCH /cart` `{note}`
- `POST /cart/voucher` `{code}` · `DELETE /cart/voucher`
- `GET /delivery-methods`
- `POST /checkout` → creates order + payment intent · `POST /checkout/:orderId/confirm` (PSP webhook also updates `payments`)

**Orders**
- `GET /me/orders?status=ongoing|completed|cancelled` · `GET /me/orders/:id` · `POST /me/orders/:id/cancel` · `POST /me/orders/:id/reorder`
- `POST /orders/track` `{order_number, email}` (public, rate-limited) → status timeline

**Seller onboarding**
- `GET /seller/application` · `PUT /seller/application/step/:n` (1–4, autosave) · `POST /seller/application/directors` · `DELETE …/:id`
- `POST /seller/application/documents` (multipart, `type`) · `POST …/documents/:id/replace`
- `POST /seller/application/submit` · `POST /seller/application/resubmit`
- `GET /seller/application/status` (timeline + open info requests)
- `POST /seller/store/activate` `{display_name, slug, agreements}` · `GET /seller/store/slug-available?slug=`
- `GET /banks` · `GET /lookups/business-types|roles|provinces|cities`

**Admin (implied, no screens)**
- `GET /admin/seller-applications?status=` · `POST …/:id/approve|reject|request-info`

**Misc**
- `POST /contact` · `POST /newsletter/subscribe` · `GET /banners?placement=`

---

## 10. Open questions for the team lead

1. **Market / currency / tax** — the design mixes Pakistan (+92, Lahore, Meezan Bank) and South Africa (Johannesburg, Gauteng, VAT). Which countries at launch? Single currency?
2. **Guest checkout** — Track Order takes order # + email, implying guests can order. Confirm.
3. **Multi-vendor fulfilment** — one order can contain items from several stores. Does each store ship separately (per-store shipments and fees) or does the platform fulfil centrally? This is the biggest schema fork.**Resolved (sprint 6).** Each store ships separately: shipping is chosen per store and snapshotted in `orders_orderdelivery` (§7d). Fees are still summed into one `Order.shipping_cents` and one payment; per-store payouts wait for the PSP.
4. **Cancellation window** — buyers can cancel "Ongoing" orders; until which status (before shipped?). Refund flow?
5. **Reviews** — guests allowed (form asks name/email)? Moderation before publishing? One review per order item?
6. **Product flags** — Featured / Best Sellers / New Arrived: manual curation or computed?
7. **Seller flow per account type** — do `individual` sellers skip Business Info / Representative / company docs?
8. **Dropdown option lists not in the design** — Business Type, Role/Designation, Bank Account Type, Contact Subject, Country/City/Province source.
9. **Payment providers** — Card, Bank Transfer, PayPal, Google Pay, Apple Pay are all shown. Which PSP(s)? Is bank transfer manually confirmed?
10. **Delivery instructions** on the address popup is marked required (*) — likely a design slip; confirm optional.
11. **Canonical category list** — seller form vs buyer filters vs landing tiles disagree (Groceries vs Food, Home & Dining vs Home & Lifestyle, Mobile vs Digital Accessories).
12. **Seller dashboard** (products, inventory, delivery setup, payouts) — referenced by "Quick Start" but not designed. Separate phase?
13. **"More Info" stage on Application Status** — drawn as a permanent 3rd stage. Backend renders it `skipped` when the reviewer never asked for more info; confirm that's the intent.
14. **Who moves `submitted → under_review`** — today an explicit admin "start review" call; should opening the application do it automatically?
15. **Colour filter source** — backend feeds "Colors" from option types named Color/Colour (not makeup Shades). Confirm.
16. **Guest checkout** is implemented (cart token + order number/email tracking) pending Q2's answer; easy to disable if the decision is members-only.
17. **Seller dashboard is now designed** (Figma 2026-09-18: Login, Dashboard, Products ×2, Inventory, Orders ×2, Order detail, Customers ×2; sidebar also lists Returns, Chat, Analytics, Notifications, Team/Managers). Supersedes Q12 — next slice. Team/Managers implies the vendor sub-roles that were deferred; do they come with this slice?
18. **Search & recommendations** — implemented with an offline lexical embedder by default. Which hosted embedding provider (Voyage recommended, OpenAI alternative) and is sending *product text only* to it acceptable? Where do "Recommended for you" / "Frequently bought together" rails live in the design (not drawn yet)?
19. **Behaviour tracking retention** — product views/searches are stored per user or guest cart token (no PII). Proposed retention: 180 days. Confirm, and whether the privacy policy needs a line about it.

**Seller dashboard (sprint 3, see `docs/sprint-3-plan.md`)**

20. **Sidebar items without screens** — Returns, Chat, Analytics, Notifications, Team/Managers (and the header badge "2", "Message Customer", "Download Invoice"). Out of scope until designed; Team/Managers = the deferred vendor sub-roles.
21. **KPI card subtitles** ("50% Average of total customers", "70% Average of total products") read as placeholder copy. Backend returns month-over-month `change_pct`; confirm that's the intended sentence.
22. **Products "Status" column** mixes lifecycle (Active) with stock (Out of Stock / Low Stock). Backend returns `status` and `stock_status` separately; UI merges.
23. **Product description ≤ 250 chars** in the wizard vs the PDP's long description + Key Benefits + How to Use. Wizard field maps to `short_description`; the rich sections stay optional — is a second "Content" step wanted?
24. **Shipping step is empty in the design.** Backend stores weight, dimensions, ships-from and handling days. Confirm, and whether per-product shipping price/free-shipping flags are expected.
    **Resolved (sprint 6).** Superseded by Q53 — a product now selects from its store's delivery options.
25. **Product moderation** — the Pending/Rejected tabs imply staff approve listings before they go live. Confirm (alternative: publish immediately, moderate after).
26. **Returned orders** — a "Returned" tab and a Returns menu exist but no return-request flow. Interim: seller marks a delivered store order returned (stock restored). Refund handling waits for the PSP.
    **Resolved in the backend (returns sprint).** Customers can request returns; sellers approve, reject and receive; staff arbitrate (7f). No screens are designed yet (Q90).
27. **Unpaid orders hidden from sellers** — orders still `pending_payment` (bank transfer not yet confirmed, PSP pending) are not shown in the seller portal. Confirm, especially for COD/bank-transfer flows.
28. **Invoices** — "Print/Download Invoice" drawn as menu items only. Backend exposes invoice data; PDF layout needs a design.
29. **Inventory "Stock" vs "Available"** — stock is decremented at checkout, so both are equal today. If "Available" should exclude unpaid/unshipped reservations, checkout must reserve instead of decrement.
30. **Customer names** — buyer side abbreviates ("Olivia M."), seller list shows both forms. Backend returns the full contact name to sellers (needed to ship). Confirm privacy stance.
31. **"Total Sales" vs "Total Revenue"** on the dashboard show near-identical figures. Backend defines Sales = booked (placed, non-cancelled) and Revenue = recognised (delivered). Confirm or drop one card.
32. **Traffic Channel widget** (visitors, socialising, active/inactive users) needs web analytics we do not collect. Omitted; wire to GA/Plausible later if wanted.
33. **Social login** (Apple/Google) on the seller Login screen — no provider configured; needs OAuth client ids and a decision on account linking.
34. **Order-level discount allocation** — vouchers apply to the whole buyer order; a store's "sales" currently use undiscounted line totals. Allocate pro-rata per store, or treat vouchers as marketplace-funded?
    **Resolved (sprint 6).** Pro-rata, see Q49.

**Multi-country markets (sprint 4, see `docs/sprint-4-plan.md`)**

35. **Launch countries** — ZA and PK are seeded as examples with plausible KYC (SA ID/CIPC/VAT; CNIC/SECP/NTN) and return windows (30 d / 7 d). Which countries actually launch, and are those policies right? Supersedes Q1.
36. **Country managers** — should staff reviewers be scoped to a country (a PK reviewer only sees PK applications/orders)? Today every staff user sees every market (`?country=` filter only). This is a role feature, together with the deferred vendor sub-roles.
37. **Translations of backend text** — error messages, status headlines and e-mails are English. Django's own strings follow the language; ours need `makemessages` and translators for af / zu / ur. Who owns translations, and is Urdu (RTL) in scope for the first launch?
38. **Bare-domain landing** — `asknget.com` without a country resolves to the default market. Should the frontend suggest a region from GeoIP / browser locale and redirect to the subdomain? (Backend does no GeoIP by design.)
39. **Per-country site settings** — footer contact details, "We Accept" logos and policy blurbs are still global (`site_settings`). Per-country overrides are a small follow-up once the content exists.
40. **Return-request flow** — the return window is now snapshotted per order and exposed as `return_eligible_until`, but buyers still cannot *request* a return (Q26). Sellers can mark goodwill returns after the window.
    **Resolved (returns sprint).** The request flow exists; goodwill returns are blocked while a customer's request for the same lines is open.
41. **One currency per market** — a store prices only in its country's currency. Is a cross-border seller (one business selling in ZA and PK) expected? Today that is two stores / two applications.

**Premium sellers (sprint 5, see `docs/sprint-5-plan.md`)**

42. **Subscriptions** — premium is a staff-flipped boolean today. When payments land: price per market, monthly vs annual, trial, proration, what happens to chat threads and analytics when a subscription lapses (proposal: analytics hidden, chat frozen read-only, nothing deleted).
43. **What else is premium?** Today: advanced analytics and the chat inbox. Candidates the team lead may want to move behind the plan: more product images, featured placement, lower commission, multiple staff accounts (Team/Managers), bulk CSV import.
44. **Buyers may message free sellers** (your decision) — they are told `seller_can_reply: false`. Confirm the copy, and whether unanswered threads older than N days should alert staff or auto-reply pointing at Contact Support.
45. **Guest chat** — guests cannot message sellers at all. Confirm, or we add order-number + e-mail thread tokens like Track Order.
46. **Thread granularity** — one thread per (store, customer). Per-order threads would suit returns/disputes better; the design's "Message Customer" sits on a customer row, so we followed that.
47. **Chat attachments** — buyers often want to show a photo of a damaged item. Not implemented; needs the same upload limits as review media and a virus-scan decision.
48. **Notifications** — the seller header badge is polled. E-mail/push for unread messages needs the background worker (perf step 6) and a quiet-hours policy.

**Analytics and wizard screens (Figma 2026-09-23, read from a .fig export — see `docs/sprint-5-plan.md` §5b)**

49. **Discount attribution per store** — "Sales Analytics" makes Discounts a headline KPI and a leg of the revenue waterfall, so a store's net sales must subtract *its share* of order-level vouchers. This is Q34 and it is now blocking: allocate pro-rata across stores, or treat vouchers as marketplace-funded and show sellers zero?
    **Resolved (sprint 6).** Pro-rata on subtotal share (`orders.services.store_discount_cents`), largest-remainder rounded so the per-store parts sum to the order's discount exactly. Who *funds* the voucher is a separate, still-open commercial question that only matters once payouts exist.
50. **Refunds and "Adjustments"** — both appear in the waterfall (R6,840 and R11,274). We record `payment_status` but never an amount, and "adjustments" is undefined: chargebacks? manual credits? platform fees? Needs the PSP and a definition.
    **Resolved (sprint 7).** Refunds are the cancelled and returned lines of paid orders (less the voucher share that came off them), computed from the orders; adjustments are staff-created ledger entries, signed (+ credits the seller). Neither needed the PSP. *Paying a refund out through a PSP* still does.
51. **Sales by Channel** (Website 67.8% / Mobile App 27.5% / Others 4.7%) — we do not record how an order was placed. Cheap to add (`Order.channel`, set by the client at checkout) but the frontend must send it and someone must own the list.
    **Resolved (sprint 6).** `Order.channel` exists with `web` (default) / `mobile_app` / `other`. **Still needed from the team:** the clients must actually send it — until they do, every order reads *Website* and the chart is honest but flat.
52. **Transactions page** — new sidebar entry, title and Export button only. Confirm it means *seller payouts and fees*, not buyer transactions; the payments sprint owns it.
    **Resolved (sprint 7).** Transactions is the seller's ledger: sales, commission, refunds, adjustments and payouts. Payouts are created and completed by staff; there is no seller "Withdraw" until a PSP exists.
53. **Per-product delivery options** — the wizard's Shipping popup lets a seller choose Express (R25), Seller Delivery (R20) and Customer Pickup (R15) **per product**. Today delivery methods are platform-level per market and the buyer picks one at checkout. Which wins? If the design does, `DeliveryMethod` becomes a per-store (or per-product) catalogue and checkout must reconcile options across a multi-store cart. Supersedes Q24.
    **Resolved (sprint 6).** The design wins, fully seller-defined (your call over the middle ground I proposed). Prices live on the *store's* option, and a product selects which of them it supports rather than pricing its own — see Q58.
54. **Product image limit** — the Media popup says "PNG, JPG upto 1-MB"; we enforce 5 MB and also allow webp/gif. Tighten to match, or is 1 MB just placeholder copy?
    **Resolved (sprint 6).** Tightened: 1 MB, PNG/JPEG only. Reverting is a one-line settings change if 1 MB proves too tight for product photography.
55. **Category request flow** — a new popup lets sellers request a category with a supporting document. New models and a staff queue (like the KYC one). In scope for which sprint?
56. **Product Analytics page is unfinished** — only the KPI row is drawn (Total Products, Top Selling, Low Performing, Avg. Product Revenue). Confirm the rest before we build to it.
    **Resolved (sprint 7).** The page was drawn in the 2026-09-29 design and is built; see Q72.
57. **Product pricing fields the wizard asks for but we do not store** — the Pricing popup has **Cost Price** (R500, alongside Selling R850 and Compare-at R1,000) and **Barcode**. Cost price implies margin reporting (and belongs in Sales Analytics); barcode implies scanning or a feed. Confirm both, then `Product.cost_price_cents` and `Product.barcode` are a one-migration change.
    **Resolved (sprint 6).** Both added. `cost_price_cents` is seller-only and no margin report reads it yet; `barcode` is not unique and nothing scans it — both are stored so the wizard stops losing what sellers type.

**Seller-defined shipping (sprint 6, see `docs/sprint-6-plan.md`)**

58. **Delivery prices are per store, not per product.** The Shipping popup shows a price beside each option on
    a *product*, which reads as "this product ships Express for R25". We priced the option on the store and let
    a product pick which options it supports, because the alternative multiplies a price per (product x option)
    and a seller with 200 listings could never keep it consistent. Confirm — if per-product prices are really
    wanted, the M2M grows a `price_cents` override column.
59. **Customer Pickup has no address.** A buyer choosing it is told nothing about where to collect, and the
    store has no pickup address or opening hours field. Needs a design decision before pickup is usable;
    today it simply costs 0.
60. **One shipping fee per order, several sellers.** The buyer pays the sum in one payment, and nothing yet
    splits it back to each seller. That split (and whether the platform takes a cut of shipping) belongs with
    payouts and the PSP, alongside Q50 and Q52.
61. **Delivery zones.** Every option has one price whatever the destination. Cross-city or cross-province
    pricing, and refusing to ship to some regions, are the obvious next asks — is either needed at launch?

**Figma 2026-09-29 — seller screens moved into a "Seller Portal" section; 7 new screens, Product Analytics finished**

62. **Transactions implies a seller ledger and payouts.** Overview KPIs are Total Sales, Pending Payout
    ("+R6,240 Processing"), Total Fees and **Available Balance — "Available to withdraw"**; the list has types
    Sale / Payout / Fee / Refund / Adjustment and statuses Completed / Processing / Refunded. That is a
    double-entry ledger per store, a payout schedule and a withdraw action — none of which exists, and all of
    which depend on the PSP (Q9, Q50). Confirm this sprint builds the ledger shape only, with amounts from
    orders, and payouts stay manual until the PSP lands. Answers Q52: the page is seller payouts and fees.
    **Resolved (sprint 7).** The ledger exists (`ledger` app). Payouts are staff-managed; a PSP will later automate completing them.
63. **The seller pays delivery fees?** The Transaction Summary deducts "Delivery Fees −R8,640" from the
    seller's payout, and the Detail shows "Delivery Fee −R40". Under the sprint 6 decision the *seller*
    sets and receives delivery prices (Q53), so the buyer's shipping should arrive as income, not leave as a
    fee. Either the platform delivers and charges the seller (a logistics model we have not got), or this
    line is the buyer's shipping passed through. Which? This decides Q60.
    **Resolved (sprint 7).** Left out by decision (2026-09-29): shipping stays the seller's income, nothing is deducted from them.
64. **Transaction Detail arithmetic does not add up.** Order R1,240 − Delivery R40 − Tax/Adjustments R18 =
    R1,182, but Net Amount shows R1,120. The R62 gap is exactly 5% of the order, which looks like a missing
    **Platform Fee** line. Confirm the commission rate (per market? per category? per plan — cf. Q43) and
    that it belongs in the breakdown.
    **Resolved (sprint 7).** Commission is a **rule the admin manages**, not a constant: store / category / market / global, effective-dated, with the rate used recorded on each fee. The 5 % is only the seeded starting rule. The detail page's breakdown reads Items, Discount, Delivery (the seller's own income), Platform fee, Net.
65. **"Update Status" on a transaction.** The detail page gives the seller a button to change a financial
    transaction's status. A seller must not be able to mark their own payout or refund complete; we will
    treat this as staff-only (or a copy-paste from View Order) unless told otherwise. The same stray button
    sits on the Notifications page.
    **Resolved (sprint 7).** Staff only. A seller can read their money, never move it.
66. **Adjustments sign.** The Transaction Summary shows Adjustments as **+R2,240** (added to payout), while
    Sales Analytics' waterfall subtracts them. Adjustments can go both ways, so we will store a signed
    amount — confirm what creates one (manual staff credit/debit, chargeback, promo subsidy).
    **Resolved (sprint 7).** Adjustments are signed (+ credits the seller), created by staff, and need a written reason.
67. **Team / Managers — roles vs permissions.** Roles shown: Owner, Manager, Inventory Manager, Order Manager,
    Marketing Manager, Finance Manager, Support Agent. Member Details lists 8 modules with a level each
    (View / View + Manage), but the Edit Permissions popup has **6 on/off toggles** (no Marketing, no
    Transactions, no View-vs-Manage). Proposal: a role is a named preset of per-module levels
    (none / view / manage), the popup edits the level per module, Owner is fixed and cannot be removed.
    Confirm, and whether members can belong to several stores. Supersedes Q17/Q20's deferred sub-roles.
    **Resolved (sprint 7).** One model: a level per section (`none` / `view` / `manage`) for all eight. Roles are `TeamRole` rows an admin edits; the popup's toggle means `manage` on or off.
68. **Invites.** "+ Invite Member" and a Pending status exist but no invite popup or accept screen. We would
    e-mail a signed, expiring link; the invitee sets a password or signs in. Confirm expiry (7 days?) and
    whether a phone OTP is required as for sellers.
    **Resolved (sprint 7).** An e-mailed link, valid `team.invite_expiry_days` (default 7, admin setting), usable once; accepting creates the account if the e-mail has none (and marks it verified). No phone OTP.
69. **"Marketing" module** appears in permissions, but sellers have no marketing features (vouchers are
    platform-wide). Is seller-created vouchers/promotions coming, or is the row placeholder?
    **Resolved (sprint 7).** `marketing` exists as a permission and guards nothing yet.
70. **Recent Activity** on Member Details needs an audit log of seller-portal actions (price change, stock
    update, CSV export). Cheap to add as one append-only table if we log at the service layer; confirm
    retention and whether the owner sees *every* member's activity.
    **Resolved (sprint 7).** Explicit entries written by the actions that matter (price changes, status moves, stock adjustments, order moves, exports, delivery options, team changes); autosave is not logged. Visible to the owner on the member's page.
71. **Notifications** — tabs Orders / Payments / Inventory / Customers / System, with New Order, Low Stock,
    Payment Received, Payout Processed, New Customer, Order Cancelled. Confirm this is in-app only for now
    (e-mail/push waits on the worker, Q48), that it is per store rather than per member, and what "System"
    covers. Low-stock uses each variant's threshold, fired once per crossing, not on every sale.
    **Resolved (sprint 7).** In-app only. Wording is editable per language (`NotificationTemplate`); read state is per person; low stock fires once per crossing.
72. **Product Analytics** is now drawn (was Q56): Top Products, a Product Performance chart (Orders / Units
    Sold / Revenue / Views per day), Product Conversion and Low Performing Products. The tables have no
    header row, and Low Performing reuses Product Conversion's placeholder rows. We read "low performing"
    as lowest conversion among products with views, not only zero-sales products. Confirm the columns.
    **Resolved (sprint 7).** Low performers are the lowest conversion among live products with at least `analytics.low_performer_min_views` views (default 20); both that threshold and the list length are admin settings, per market or store.
73. **Returns** is still a sidebar entry with no screen (Q26, Q40). The API exists now (7f); the screens do not (Q90).

**Figma 2026-10-09 — the Admin portal (13 new screens; see `docs/sprint-7-plan.md` §8)**

74. **Is the admin portal next, and who is an admin?** Everything staff-facing hangs off `is_staff` today. The
    design's "Admin" is one role; a marketplace usually needs several (support, finance, catalogue). Supersedes
    Q36 once answered. Proposal: ship the portal with the single staff role, and design roles with the team.
75. **"+ Add New Seller" (staff creates a seller).** There is no such flow: sellers apply and are reviewed. Does
    staff entry skip KYC, or pre-fill an application that still needs documents? Proposal: an application
    created on the seller's behalf that still goes through review; a waiver needs a written reason on record.
76. **What is an "Inactive" seller?** The Sellers tabs are Active / Pending Review / Suspended / Inactive. Pending
    and Suspended map to statuses we have; Inactive does not. Proposal: an active store with no order and no
    sign-in for 90 days — computed, never stored.
77. **Product "Suspended".** The Products tabs include Suspended (386) and a Returned (12), and the detail page
    has a Suspend button. We have no suspended product state (draft, pending, active, rejected, archived).
    Proposal: add `suspended` (staff only, hides the product, the seller sees why). *Returned* looks copied from
    the Orders tabs and is treated as a slip.
78. **Dashboard visitors and active/inactive users** need web analytics we do not collect (same as Q32). Omitted
    unless a tracker is chosen.
79. **Two formats for one thing.** Seller screens show a transaction as `TXN-10482`, the admin order page as
    `TXN-20260930-28501`. Proposal: the dated form everywhere.
80. **Category form wording.** The Add Category popup labels its description "Product Description" (a slip) and
    its order field "Display Order / Units". Treated as category description and sort order. Its image limit is
    2 MB JPG/PNG, against 1 MB for product images — kept as drawn.
81. **Hiding a category.** The Subcategories screen says "Hidden from customers — 16 products not visible in
    store". Proposal: hiding cascades — the category, its subcategories and their products leave the storefront
    and the seller category picker; nothing is deleted and sellers see why. Confirm.
82. **"+ Add New Product" inside Category Details.** Staff would create a product for a seller. We have no
    staff-authored products (a product belongs to a store). Proposal: the button opens the seller's wizard
    only if staff pick a store; otherwise omit it.
83. **Seller rating and the "Trusted Seller" badge** (4.8 / 5, 126 reviews). We have product reviews, not seller
    reviews. Proposal: the rating is the mean of approved reviews across the store's products; the badge needs a
    rule from the team (for example 4.5+ with 50+ reviews).

**Sprint 7 — decisions taken while building (please confirm)**

84. **The payout hold starts at delivery, not at payment.** The plan said "placed + the return window", but money
    cannot be released before the goods arrive, so a sale stays *held* until the store's goods are delivered and the
    hold then starts (`payouts.hold_mode`). `none` is the only mode that releases at payment. Confirm.
85. **Two refund figures, on purpose.** The ledger's refunds are cash: the whole sale including the delivery charge
    that goes back. Sales Analytics' refunds are item value net of voucher, so it reconciles with Gross Sales. They
    differ by the delivery charge.
86. **Defaults taken, reversible:** team management is owner-only and seats are unlimited until `team.max_members` is
    set; adjustments are staff-created; `marketing` guards nothing; notifications are in-app only; transaction
    references are `TXN-YYYYMMDD-<n>` everywhere.
87. **A premium plan could carry a lower commission (Q43)** — today that is a store-scoped `CommissionRule` set by
    hand; tying it to the plan automatically is a small follow-up if wanted.
88. **Backfilled history uses today's rules.** `backfill_ledger` posts old orders with the commission rates and hold
    policy in force now, not those of the time, and its references carry today's date though the entries are dated to
    the order. Only matters for data that existed before the ledger.
89. **Seller portal address.** Invite e-mails link to `PORTAL_URL`, falling back to `STOREFRONT_URL`; until one is set the
    link has no host. What is the portal's address?

**Returns sprint (2026-10-13; `docs/returns-plan.md`)**
90. **Return screens are not designed.** Needed: buyer "Return item" entry on View Details, the return form (items, reason, note, photos),
    return status and timeline, a Returns tab on My Orders and shipping-back instructions; seller Returns list and detail with approve / reject /
    received; admin Returns queue (disputes, damaged parcels, overdue) and a Settings screen for the return policy, reasons and category rules.
91. **Real policy numbers.** Seeded values are placeholders: ZA 30 days change of mind and 180 days defects, PK 7 and 14. South African online
    law is usually a 7-day cooling-off right for change of mind plus a 6-month right for defective goods; Pakistan has no national window we could
    find (sellers set 7-14 days). Business and legal sign-off needed per market, and which categories are non-returnable (opened cosmetics).
92. **Courier.** Return pickups go through `returns/couriers`; until the technical lead's courier API arrives every market uses `manual` (the buyer
    posts it and types the tracking number). Questions for that integration: does it book pickups from the buyer's address, issue a prepaid label,
    push webhooks or need polling, and does the same API book the seller's outbound shipments (the interface is meant to carry those too)?
93. **Refund to the card.** No PSP yet: an accepted return posts the ledger refund and marks the buyer's refund `pending`; staff pay it and mark it paid
    (`/admin/returns/{id}/refund-paid`). Confirm that is acceptable until a provider is chosen.
94. **Defaults taken, reversible:** whole lines only (no partial quantity); sellers approve every return (auto-approve is a setting); delivery is refunded
    only when the seller is at fault and every line of that store comes back, once; a restocking fee applies to change of mind only and is 0 until set;
    a damaged parcel waits for a staff decision; a rejection can be disputed once and staff's decision is final (a setting turns disputes off).
95. **Buyers have no notification inbox,** so buyer-facing return events are plain e-mails (English). Per-language e-mail templates and an in-app inbox are follow-ups.
96. **Exchange / replacement** as a resolution is not built (refund only).
97. **Defaults changed by the bug fixes (`docs/bugfix-plan.md`), reversible:** a final rejection blocks the same lines only for the same family of
    reason (change of mind vs the seller's fault) unless the seller already received the goods; a parcel staff refuse a refund for goes back to the
    buyer (said in the e-mail, timeline and seller notification; a tracked return-to-customer shipment is not built); sellers approve or reject only
    new requests. **A refund after a payout is a debt, not a hold:** holding every sale for the 180-day defect window would starve sellers, so a late
    refund may take the balance below zero, payouts are refused until later sales cover it. Confirm.
98. **Seller documents are private.** They are served only through signed links that expire (`SIGNED_FILE_TTL_SECONDS`, 1 hour); nginx refuses
    `/media/seller_documents/`. Documents uploaded before keep their old file names on disk but are reachable only the same way.

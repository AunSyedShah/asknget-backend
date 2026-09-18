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
- Money: integer minor units (`*_cents`) + `currency` (ISO-4217). Design shows `$` only. ❓ single or multi-currency.
- Phone: E.164 string. Design shows `+92` (PK) and South African addresses (Johannesburg / Gauteng) — ❓ target market(s); affects province/city lookups and VAT/tax fields.
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

### `delivery_methods`
`id`, `name`, `eta_min_days`, `eta_max_days`, `price_cents`, `is_active`. Seen: Standard (5–7 days), Express (2–3 days). ❓ flat fee vs per-seller/per-zone; multi-vendor orders may need per-store shipments.

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
| delivery_method_id | FK, nullable | |
| delivery_method_snapshot | jsonb | name + ETA at time of order |
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
3. **Multi-vendor fulfilment** — one order can contain items from several stores. Does each store ship separately (per-store shipments and fees) or does the platform fulfil centrally? This is the biggest schema fork.
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

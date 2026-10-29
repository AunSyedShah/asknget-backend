# Seller product import from CSV: draft plan

Requested 2026-10-27, as an addition to [`ai-product-moderation-plan.md`](ai-product-moderation-plan.md). The seller uploads one
`.csv` instead of filling the 5-step wizard for every product. **The automatic checks run after the upload**: the file is read, each
product is created and submitted, and the moderation pipeline decides (live, human review, or rejected with a reason). The seller
gets one report at the end. Categories stay human-approved: the CSV can only use categories that already exist.

> The lifecycle (rows are inserted unpublished, the seller sees "under review", checks run in the background, one summary notice per
> batch) is defined in [`product-review-flow-plan.md`](product-review-flow-plan.md); this import uses that engine.

I read this as "the upload is the trigger", not "check first, then upload". If you meant that the checks should *also* apply to
manual single-product submits, that is the moderation plan and is already covered.

## A. What the seller sees

1. **Download the template** (one example row per product shape, with the right headers) and, if wanted, **the list of categories
   and brands** they may use.
2. **Upload** the file. The server reads it **at once** and answers in seconds with a per-row report: how many products and variants
   it found, which rows are wrong and why, in plain words ("row 14: category 'Skin Care' not found; closest: 'Skincare'").
3. **Confirm** ("Import the 46 valid products, skip the 3 with errors"). The seller can also tick "do this automatically" and skip
   the confirmation (J-2).
4. The system creates the products, fetches their pictures, submits them, and the **moderation checks run**. The seller sees progress
   and then a final report: *live now*, *waiting for a person*, *rejected (reason)*, *needs attention (missing picture, etc.)*.
5. **Download the result CSV** (their own columns plus `result`, `message`, `product_id`), fix the failed rows, and upload only
   those. Uploading the same file twice does not create duplicates.

## B. The CSV format

**One row per variant, products grouped by `product_key`** (a key the seller makes up, e.g. their own style code). The first row of a
group carries the product-level columns; later rows of the same group carry only variant columns. A product with no options is a
single row.

| Column | Required | Notes |
|---|---|---|
| `product_key` | yes | groups rows; also the idempotency key (stored on the product as `import_key`, unique per store) |
| `name` | yes | |
| `category` | yes | slug or path (`skincare/face`); matched against active categories only; never creates one |
| `brand` | no | must exist; unknown brand is a row error that lists close matches |
| `short_description`, `description_title`, `description` | no | plain text; HTML is **stripped** (see G) |
| `tags` | no | separated by `\|` |
| `benefits`, `usage_steps` | no | separated by `\|`, become the Key Benefits and How-to-Use sections |
| `price` | yes | decimal in the store's currency (`149.99` or `149,99`), not cents; currency is the store's, never typed |
| `compare_at_price`, `cost_price` | no | compare-at must exceed price (same rule as the wizard) |
| `barcode` | no | |
| `option1_name` / `option1_value` … `option3_*` | no | e.g. `Size` / `30 ml`; same option names across a group's rows |
| `sku` | no | blank = generated; **SKUs are unique across the whole platform today** (see K), a clash is a row error |
| `stock_qty` | no | whole number, default 0 |
| `preorder` | no | `yes/no` |
| `weight_grams`, `length_mm`, `width_mm`, `height_mm`, `ships_from`, `handling_days` | no | the wizard's Shipping step |
| `image_urls` | for going live | up to 8 public `https` links, separated by `\|`; first is the primary picture |

Reading rules (what makes real-world files work): UTF-8 with or without BOM, falling back to Windows-1252; delimiter `,` or `;`
(Excel in many locales saves with `;`); headers case-insensitive with aliases (`Title`→`name`, `Price`→`price`); blank lines and
trailing empty columns ignored; decimal comma accepted; a header we don't know is ignored with a warning, never an error.

## C. Pipeline, per product

```
upload ─▶ parse + validate (synchronous, pure Python, no database writes)        → row report
confirm ─▶ create product + variants + content (draft)                            one transaction per product
        ─▶ fetch images (slow; https only, guarded)                              resumable step
        ─▶ completion check (same rules as the wizard: name, category, image, price, variant, stock)
        ─▶ submit ─▶ moderation (trigger = import) ─▶ live | pending (person) | rejected
```

- **Each product is its own transaction**: one bad product never rolls back the others (`on_error=skip` is the default; `abort`
  stops at the first error and creates nothing, for sellers who want all-or-nothing).
- A product whose pictures fail or are missing stays a **draft** with a clear message; the seller adds pictures in the wizard and submits.
  It is never silently dropped and never submitted incomplete.
- Moderation uses the **same engine and the same decision matrix** as a manual submit. Import adds extra gates (section E).
  If moderation is `off` for the market, imported products simply enter the normal human queue.

## D. Running it where we deploy

- **AWS EC2 first (owner decision 2026-10-27), with the `compose.yaml` stack.** There is a real worker, so the import is a normal
  background job: a new `importer` service (or the `reviewer`) runs `process_import_jobs --watch`, takes confirmed jobs, creates
  products, fetches pictures, submits, and the review engine decides. The seller's browser only **uploads, confirms and polls** the job;
  closing the tab does not stop it. Upload and validation are synchronous and fast (pure Python); nginx allows 30 MB bodies by default
  (raise it for the import route only, for a ZIP) and gunicorn times out at 60 s, so validation of a few thousand rows is the longest
  request. Limits start at about **5,000 rows** per file. A **ZIP of pictures** next to the CSV is possible from the first version
  (picture file names in the CSV), because no 4 MB request cap applies on EC2 (`UPLOAD_CAP_BYTES` is 0 there). Uploaded pictures go
  through the same `safe_image` path as the wizard (1 MB, 8 per product, downscaled if larger).
- **Vercel Preview (demo only):** keeps the earlier shape: about 500 rows, 4 MB, and a resumable `POST .../run` that does a bounded
  slice per call, because it has no worker. The same job code serves both; only who advances it differs.
- The uploaded file is kept in **private storage** (`product_imports/`, added to the never-served-from-`/media/` list) for 30 days
  for support and re-runs, then deleted by the retention job.

## E. Safeguards specific to bulk (this is where abuse concentrates)

- **Moderation gates for imports:** the new-store probation (first N products) applies to imported products too, so a brand-new store
  that uploads 500 products gets **500 human reviews, not 500 auto-approvals**. Imports from trusted stores follow the normal rules.
- **Velocity gate:** a cap on auto-approvals per store per day (default 100); the rest wait for a person. A cap on new products per
  store per day (default 1,000) and on uploads per hour (default 10).
- **Batch similarity:** the moderation engine's copy-paste check also runs **across the rows of the same file** (200 identical
  descriptions, one description with the name swapped) and flags the batch.
- **Admin queue groups by import:** rows carry `import_job` so staff can filter a batch, sort by risk, and approve or reject many at
  once with a reason code. Otherwise one large upload can swamp the queue.
- **Sampling audit** applies as in the moderation plan, and imported products are weighted higher in it.

## F. Data model (new)

- `catalog.ImportJob`: store, user, filename, `sha256` (the same file twice is refused with 409 unless `force`), stored file, options
  (`on_error`, `submit`), `status` (`validated|running|paused|done|failed|cancelled`), counts per state, error summary, timestamps.
- `catalog.ImportRow`: job, `row_no`, `product_key`, `state` (`invalid|ready|created|awaiting_images|submitted|live|review|rejected|failed`),
  `errors` and `warnings` (lists of `{column, code, message}`), `product` (nullable), raw values (size-capped).
- `catalog.Product.import_key` (nullable) with a unique constraint per store (when not null), the idempotency key for re-uploads.
- `moderation.ProductCheck.trigger` gains `import`; `ProductCheck` stores `import_job` for batch filtering.
- Validation of the parsed rows uses **pydantic** (a `ProductRow` model, per-column error codes), so the same schemas generate the
  error messages the seller reads.

## G. Security and robustness

- **CSV injection:** product fields typed by sellers can start with `=`, `+`, `-`, `@` and become a formula when someone opens an admin
  or seller export in Excel. The result CSV, and every existing export that includes seller text (inventory, orders), prefix such cells
  with `'`. I will check the existing `export.csv` writers for this.
- **HTML and script in text:** descriptions are stored as plain text; tags are stripped on import (Shopify and Woo exports contain HTML).
- **Image fetching is a server-side request, so it is guarded against SSRF:** `https` only; resolve the host and **refuse private,
  loopback, link-local and cloud-metadata addresses** (checked again on every redirect, maximum 3); streaming download with a
  size cap and 5-second timeout; the type is detected from the bytes (`safe_image`), not from the header or file name; at most 8
  images per product; a per-job cap on total bytes and requests; no cookies or credentials sent. Images over the 1 MB wizard limit are
  **downscaled** during import instead of rejected, so a phone-camera URL still works.
- **Parser limits:** maximum row count, cell length and column count; a file that is not text (a renamed `.xlsx`, a zip bomb) is
  refused with a plain message; quoted newlines handled; no `eval` anywhere; rows are processed lazily.
- **Permissions and audit:** the same store-team permission that may create products; each upload is written to the store activity
  log (`team.log(..., "import_products", ...)`, as exports are) so the owner sees who imported what.
- **Money precision:** parsed with `Decimal`, rounded to the currency's minor unit, rejected beyond 2 decimals rather than rounded silently.

## H. API (seller portal, `StoreAuth`)

| Operation | Purpose |
|---|---|
| `GET /seller/products/import/template.csv` | the template |
| `GET /seller/products/import/reference` | usable categories (slug, path) and brands |
| `POST /seller/products/import` | multipart `file`, `on_error`, `submit`, `confirm`; validates and returns the job + first page of row errors |
| `POST /seller/products/import/{id}/commit` | start creating (the two-step flow) |
| `POST /seller/products/import/{id}/run` | do one bounded slice; returns progress; safe to repeat |
| `GET /seller/products/import` | history |
| `GET /seller/products/import/{id}` | counts per state |
| `GET /seller/products/import/{id}/rows?state=` | paged rows with errors |
| `GET /seller/products/import/{id}/report.csv` | the result file |
| `POST /seller/products/import/{id}/cancel` | stop; already created products stay |

All with documented error responses, unique OpenAPI names, and a test that every operation declares an auth class. Notifications use
the Sprint 9 bus: one `import.finished` event to the seller (bell and e-mail) with the counts, and a staff event when a batch lands in
the review queue.

## I. Phases

| Phase | Scope | Depends on |
|---|---|---|
| CSV-1 | Template and reference, parser and pydantic row validation, dry-run report, `ImportJob`/`ImportRow`, create **draft** products idempotently, result CSV, limits, injection and HTML guards, `import_key` migration | nothing (can ship before any AI) |
| CSV-2 | Image URLs with the SSRF guards, resumable `run` endpoint, completion check, **submit** after images; with moderation off the products enter the human queue; admin queue grouping by import and bulk approve/reject | CSV-1 |
| CSV-3 | **Moderation after upload**: `trigger=import`, bulk gates (probation, velocity, batch similarity), the seller's final report with reasons, `import.finished` notification | CSV-2 + moderation Phase 1 (rules) at least |
| CSV-4 | Update existing products by SKU or `import_key` (price and stock), ZIP pictures and larger files on AWS, Shopify/WooCommerce export compatibility, "export my products as a CSV I can re-import" | AWS worker for the large parts |

CSV-1 and CSV-2 are useful even before the AI exists, but they would send more work to the human queue, which is why CSV-3
(the gates and batch grouping) should ship with them or immediately after.

## J. Decisions needed from the owner (my recommendation first)

1. **Pictures in the first version:** on EC2 both are possible. Recommend **image links first** (sellers already have them on their own
   site, Drive or CDN; no large upload) and a **ZIP of pictures** in the same release or the next, because a seller moving from a
   spreadsheet often has pictures on a PC and not online.
2. **Auto-run after upload?** Recommend a **two-step by default** (upload → report → confirm), with a "run automatically" switch for
   sellers who trust their file. The check on each product runs either way after confirmation.
3. **Limits to start:** 5,000 rows per file on EC2 (500 on the Vercel Preview), 1,000 new products per store per day, 10 uploads per hour, 100
   auto-approvals per store per day. All are settings.
4. **Create-only in the first version**, update-by-SKU in CSV-4. (Updating is where mistakes are expensive: a wrong price on a
   live product.)
5. **New stores:** all imported products go to a person until the probation count is reached (consistent with the moderation plan).
6. **Only `.csv`**; Excel (`.xlsx`) is a later add-on. The template opens in Excel and Google Sheets and saves as CSV.

## K. Suggestions and open points

1. **Shopify and WooCommerce compatibility** (CSV-4) is probably the biggest time saver for sellers who already sell elsewhere:
   mapping `Handle, Title, Body (HTML), Vendor, Type, Tags, Variant SKU, Variant Price, Image Src` to our columns means "export from
   Shopify, upload here". Needs only a column-mapping table and the HTML stripper.
2. **Make export and import round-trip:** the existing `GET /seller/inventory/export.csv` is one line per variant; a "products export"
   with the import columns lets a seller edit prices in a spreadsheet and re-upload them (with CSV-4).
3. **SKU uniqueness is global** (`ProductVariant.sku` is unique across the platform), so two stores both using `BB-01` clash and the
   second store sees an error. For import this is a real annoyance. Fixing it means making SKU unique **per store** (a migration and a
   check of every place that looks a variant up by SKU). I recommend doing it, but it is a separate change; until then the report says
   exactly which SKU is taken and offers "leave blank to generate".
4. **Pre-check reuse:** the rules engine from the moderation plan's seller pre-check validates each row's text in the dry run, so a
   seller learns about a banned phrase or a phone number in the description **before** the products exist.
5. **Report is the product:** most of the value is a clear row-level report. I will spend more of the effort on messages and the
   result CSV than on the parser.
6. **Frontend work:** upload dropzone, template and reference downloads, validation table with filter by error, progress bar driven by
   `run`, final report with a download. I will write the API contract first and hand the frontend session the pack.

## L. Tests that must exist

- Parser: BOM, `;` and `,` delimiters, quoted newlines and commas, Windows-1252 fallback, blank lines, unknown headers, decimal comma, aliases.
- Grouping: variants of one `product_key` become one product with the right option types and values; conflicting product-level
  columns across a group's rows are reported; option names must match across rows.
- Validation: unknown category and brand (with suggestions), compare-at not above price, bad numbers, SKU already taken, too many images,
  too many rows, wrong file type; each gives a column-level code and a plain message.
- Idempotency: the same file twice is refused; the same `product_key` twice does not create a second product; `run` twice does not
  double-create or double-submit; `cancel` leaves finished products intact.
- Atomicity: one failing product does not roll back the others; `on_error=abort` creates nothing.
- Images: private, loopback, link-local and metadata addresses are refused, also via redirect and DNS that resolves privately; oversized,
  non-image and slow responses are refused; large images are downscaled; a failed image leaves a draft with a message, never an
  incomplete submission.
- Moderation: an imported product from a probation store never auto-approves; velocity cap; batch similarity flags identical rows;
  moderation off sends products to the normal queue; trigger is recorded as `import`.
- Security: `=cmd` cells are neutralised in the result CSV and the existing exports; HTML in descriptions is stripped; another store
  cannot see or run a job (404); the stored file is never served from `/media/`; a member without the product permission is refused.
- Resumability on a small time budget: a job larger than one slice completes across several `run` calls, in any order of retries.
- Query budgets for the job and rows endpoints (Zeal N+1 check); OpenAPI names unique; permission declared on every operation.

## M. What I verified and what I did not (2026-10-27)

- Read in the code: the wizard services (`portal/products.py`: `create`, `set_options`, `set_content`, `add_image`, `completion`,
  `change_status`, `approve`, `reject`), image limits (1 MB, 8 images, JPEG/PNG), the Vercel 4 MB upload cap, global SKU uniqueness
  on `ProductVariant.sku`, and the existing CSV exports for orders, inventory and customers.
- Not yet checked: how the existing exports escape cells (formula injection); which team permission corresponds to "may create
  products"; the exact fields on `Category` and `Brand` available for matching (slug, path); Vercel's function time limit on the
  plan in use (decides the slice size); whether `safe_image` can downscale rather than only validate.
- Every number here (500 rows, 25 per slice, 1,000 per day) is a starting proposal to tune with real use.

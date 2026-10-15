# asknget: brief for the agent that builds the frontend

> **Approved by the owner 2026-10-16** (the decisions are recorded in section 8). Written by the backend session so that a fresh Claude
> session can build the frontend against this backend while the backend keeps changing. It is the *entry point*: it says what
> the product is, where everything is, and how the two sessions stay in step. It does not repeat
> [`frontend-handoff.md`](frontend-handoff.md), which holds the API contract and the generated endpoint index; read that second.
> If anything here disagrees with the running API, **the API wins**: `GET <base>/openapi.json` is the contract, `<base>/docs` is its UI.

## 0. Read in this order (about 20 minutes)

1. This file.
2. [`frontend-handoff.md`](frontend-handoff.md) sections 3 to 5b: headers, auth, errors, lists, money, what changed recently.
3. **`https://asknget-backend-git-develop-aun-syed-shahs-projects.vercel.app/api/v1/openapi.json`** (public; the Swagger UI is the same URL ending `/docs`). This is the exact contract: generate typed clients from it (for example `openapi-typescript`); do not hand-write response types. Facts about the document that a generator will trip over:
   - **Every path already starts with `/api/v1`** (for example `/api/v1/cart/items`) and the document has **no `servers` entry**. Set the client's base URL to the **origin only** (`API_ORIGIN`, section 7a), never to `.../api/v1`, or every call becomes `/api/v1/api/v1/...`.
   - It declares 234 operations, each with a unique `operationId` (long, like `catalog_request_api_approve_category_request`; rename them in the generator's config if you want shorter names) and 312 schemas.
   - **It does not declare the custom request headers** (`X-Region`, `X-Language`, `X-Timezone`, `X-Store`; `X-Cart-Token` only on the cart calls). They are in `frontend-handoff.md` section 3: add them in one place in the client.
   - **Many operations declare no error response** (82 have no 4xx). Every error is `{"detail": "..."}` (422 adds `errors[]`), so model errors once, globally; do not rely on the document for them.
   - Security schemes are `JWTAuth`, `OptionalJWTAuth`, `StaffAuth`, `SellerAuth`, `StoreAuth`: all are `Authorization: Bearer <access_token>`; they differ only in who is let in.
   - It describes Preview's code at the moment you fetch it. Re-fetch at the start of every session. If your session cannot reach the internet, ask the owner for a copy (the backend session exports one on request).
4. [`api-changes.md`](api-changes.md): the log of backend changes you must react to.
5. The Figma screens you are about to build (section 6), never the whole file.

## 1. What asknget is

A **multi-vendor marketplace**: many independent sellers sell to buyers in several countries, and the platform (asknget) takes a commission.

Three audiences, three frontends (all in one Figma file):

| Surface | Who | What they do | Design | API |
|---|---|---|---|---|
| **Storefront** | buyers (and guests) | browse, search, cart, checkout, track, return, review, wishlist, chat with sellers | complete | complete except payments |
| **Seller portal** | store owners and their team | apply (KYC), list products, inventory, orders, returns, payouts, analytics (premium), chat, team, delivery options | complete | complete |
| **Admin portal** | asknget staff | review seller applications, orders, products, returns, payouts, categories, customers, transactions | 13+ screens added 2026-10-09 and more since | **partial** (section 5) |

Markets today: **South Africa (ZA, ZAR)** and **Pakistan (PK, PKR)**. A store sells in exactly one country and currency; a cart holds one market;
language, timezone and currency come from the market (section 3 of the handoff). Money is integer minor units (`*_cents`), never floats.

## 2. The backend in one page

Django 6.1 + django-ninja, Postgres (SQLite locally), `uv`. Repo: `github.com/AunSyedShah/asknget-backend`, Django root `backend/asknget/`.
One app per concern; every endpoint lives in an `api.py`:

| App | Owns |
|---|---|
| `accounts` | registration, login (JWT), profile, addresses, saved payment methods (never a card number), **email OTP**, **WhatsApp phone verification (user messages us; QR or button)** |
| `catalog` | categories (two levels), products, variants, reviews, wishlist, **category requests** (a seller asks, staff decide) |
| `discovery` | search, suggestions, recommendations, product events |
| `orders` | cart (guest and member), **per-seller delivery with a free-over-threshold-or-fee rule**, checkout, order lifecycle, cancel, track |
| `returns` | customer returns, per-market return rules, refunds, courier seam (the technical lead's courier API plugs in here) |
| `sellers` | seller application and KYC (per country), stores, documents (private, signed links) |
| `portal` | everything the seller portal calls (`/seller/*`), plus some `/admin/*` |
| `ledger` | append-only money: sales, commission, refunds, payouts |
| `markets` | countries, regions, languages, per-country KYC and return policy |
| `config` | admin-tunable settings with global / country / store scope |
| `notifications`, `team`, `chat`, `marketing` | in-portal notifications, team members and roles, buyer-seller chat, banners and vouchers |

Rules the UI must respect (details in the handoff): premium-only areas answer `403 premium_required`; the seller's work is scoped by store (`X-Store`);
a seller cannot create categories, they request them; an order keeps a snapshot of what it was charged; refunds go to the original payment only.

## 3. Environments and access

| Where | Base URL | Data | Use it for |
|---|---|---|---|
| **Preview (`develop` branch)** | `https://asknget-backend-git-develop-aun-syed-shahs-projects.vercel.app/api/v1` | staging database with demo data | **build and test against this**; Swagger at `/docs`; stable URL, public |
| Production (`main`) | `https://asknget-backend.vercel.app/api/v1` | real, empty so far | **never**: the owner merges to `main` |
| Local | `http://localhost:8000/api/v1` (`uv run poe dev`) or `:8080` (Docker) | your own | offline work; `uv run poe seed` creates demo data |

- **Demo accounts** (staging and local): `customer@asknget.demo`, `seller@asknget.demo`, `admin@asknget.demo`; the demo password is in `backend/README.md`. They exist **only** on staging and local; never create them on production.
- **CORS:** a browser app on another origin is refused until its origin is in the backend's `CORS_ALLOWED_ORIGINS` (not set yet on Preview or Production). Ask the owner to add yours; do not work around it, and do not use `credentials: 'include'`.
- **Free-tier infrastructure** (Neon, Vercel Hobby): keep test traffic light; do not loop requests or load-test.
- **Uploads on Vercel do not work yet** (the disk is read-only; object storage is not set up). Any call that stores a file (avatar, product images, review photos, return photos, KYC documents, category-request image) fails on Preview; work against a local backend for those, or mock them.
- **Never** put credentials, tokens or `.env` contents in code, logs, commits or chat.

## 4. What is built, what is not

**Built and tested (616 backend tests):** everything in the app table above.

**Not built, do not wire (render static, empty or disabled, and tell the owner):**
- **Payments**: `POST /checkout` makes the order `pending_payment`; there is no card tokenisation or confirmation.
- **Courier / tracking number / Live Track Order**: waiting for the technical lead's courier API.
- **Wallet refund** (the Return Order popup offers "Wallet"): not decided; only "Original payment" works.
- **Admin portal API gaps**: dashboard KPIs, category management (add, edit, deactivate, reorder), customers, transactions overview, applications screens beyond seller-application review, account information, recent activity.
- **Customer order screens of 2026-10-14**: backend work is planned (`sprint-8-plan.md` section 2): cancel reasons list, card brand and last four digits on an order, estimated delivery date, tracking fields. Until then build the screens from the data that exists.

**Known backend bugs** (the backend session will fix; do not code around them without telling the owner): the first `POST /cart/items` from a brand-new guest returns an empty cart in its response (the item *is* saved; read the cart again with the returned `cart_token`).

## 5. Reading the Figma design (a fresh session has no memory of this)

One Figma file, three surfaces, **desktop frames only** (no mobile design; see handoff section 1 for the responsive defaults).

**The frontend is built from pictures, so pictures come first. The frontend session never calls Figma itself.** The pictures live in
**`Figma/frames/`** at the root of the backend repository (git-ignored; the owner's export, catalogued), **split by audience into `customer/` (28 screens), `seller/` (41) and `admin/` (22)**: one PNG per frame, named
`<Frame-name>__<node-id>.png`, and **`Figma/frames/manifest.json`** listing every frame (node id, name, audience, file, size, where it came from) and any frame still missing; **`Figma/frames/INDEX.md`** is the same as one readable table per audience.
The frontend session reads that folder read-only (or the owner copies it into the frontend repo's git-ignored `design/`). The text outlines in
`docs/figma/screens/` give exact copy, labels and component states next to each picture. **Start from `manifest.json`, not from the file names.**

**State on 2026-10-16: 89 of the 91 frames are in `Figma/frames/`** (customer 28 of 28, seller 41 of 41, admin 20 of 22) (all 1x, 1920 px wide, desktop only). Missing: **Account Information** and **Recent Activity**
(new admin screens; export them, or the backend session renders them once the Figma image limit resets, about 26 hours after 2026-10-16). Two frames are not what
their Figma name says: `312:3402` is named "My Account - Payments Methods" in Figma but shows the **Password Manager** screen (catalogued as that), and the design
has two frames each called "Products", "Orders", "Customers" and "Customer Detail" (a seller one and an admin one); trust the node id, which the manifest has.

The Figma file has two pages: "Web UI Design" (the 91 screens, all three audiences mixed) and "Components". **The owner checked the Components page on 2026-10-16: it holds nothing usable (random pictures, no design system).** So there is **no component library in the design**: derive the shared components (buttons, inputs, cards, tables, modals, navigation, form fields, status pills) **from the screens themselves**, once, in a `components/` folder of the frontend, and reuse them. Where two screens draw the same thing differently, pick one, use it everywhere, and note the inconsistency in `DESIGN_NOTES.md`. The icon sheet the designer exported lives at `Figma/Icons.png` (not in `frames/`).

**How the pictures are produced, in order of preference. The first that works wins; none depends on the others.**

1. **The owner exports from the Figma desktop app, then the catalog tool names them (the normal way; no API, no token, no limit, tested 2026-10-16).**
   In Figma: open the file, click the canvas, Ctrl+A, in the right panel under **Export** add **PNG at 1x**, click **Export N layers**, save into `Figma/` at the
   repo root. Then, from the backend repo, `backend/.venv/Scripts/python docs/figma/catalog_exports.py Figma` (or `uv run --with pillow python docs/figma/catalog_exports.py Figma`). It matches each file to
   its frame by name, cuts any section that Figma exported as **one huge image** (the "Seller Portal" section arrives as a single 29,660 x 8,078 picture of 41 screens)
   into its screens at exact 1x pixels, and writes `Figma/frames/` and the manifest. Do it again after every design change. If the designer adds or moves screens inside
   that section the tool says the count no longer matches and refuses to guess: then `docs/figma/seller-portal-order.json` has to be re-checked.
2. **API renders from the owner's FRESH DUPLICATE (for a few frames, or when a re-export is inconvenient).** `python docs/figma/snapshot.py --file <duplicate url> --index`,
   then `--render-all` or `--render <frame>`. The tool fetches 25 frames per call, skips frames already rendered for the current version, and is resumable. Why a
   duplicate: the original lives on someone else's Starter plan and locks after a few calls (2026-10-16: the image API returned 429 on the second call and again about
   a day's worth of waiting later; the text API earlier for about 62 hours). A copy on the owner's own plan has its own budget (verified 2026-10-16). **Node ids survive
   duplication.** The existing duplicate was last saved 2026-09-23 and has none of the October screens; make a new one (right-click the original, Duplicate) when needed.
3. **Browser capture of a single frame (emergency only).** The backend session can open the file in the owner's own logged-in browser and screenshot one frame.
   Slower and lower fidelity than 1 or 2; not for bulk.
4. **`.fig` export (structure only, complementary).** `uv run --with zstandard python docs/figma/snapshot.py --from-fig <file>.fig` decodes the scene graph into the text
   outlines: no token, no rate limit. **It has no pictures, colours, fonts or spacing, so it is never the way to see a screen.** Use it only to refresh the text outlines
   while the text API is locked.

**The token.** `FIGMA_TOKEN` is the owner's personal access token. The tool reads the backend repo's root `.env` (git-ignored), then the environment. **Never print it, paste it into a file, or commit it.** If it returns `401`, the token expired (they last up to 90 days): ask the owner to make a new one and put it in `.env` themselves.

**Rate limits (Starter plan).** `--check` is cheap and tells you whether the design changed. `--index` is one small call. The text endpoints (`--nodes`, `--only`)
are cost-based: about 10 full frames can lock them for days. The image endpoint counts **calls**: batch big, and on a `429` **stop and never retry in a loop**
(the tool saves its progress and exits with code 2; run it again later). If the original is locked, switch to the duplicate or to the manual export.

**What changed in the design on 2026-10-14** (new or re-saved; only four were read, as renders): customer **View Details** (full page), **Order Cancel**, **Return Order**, **Track order**, **Live Track Order**, **Leave Review**, **Order Placed**; admin **Returns & Refunds**, **Return Details**, **Applications**, **Application Details**, **Account Information**, **Recent Activity**, **Customers**, **Transactions**, **Customer Detail**. The index of all 91 frames is `docs/figma/page-index.json`; node ids in it work with `--render`.

**Treat the design as a hypothesis.** Where it disagrees with the API or with itself (for example prices shown as both `$` and `R`; a typo, "Fount better price"), do not silently choose: write it down in `BACKEND_ASKS.md` or `DESIGN_NOTES.md` and ask.

## 6. Working in parallel: the protocol

Two sessions, two repositories, one owner who brings them together. The rules that keep them from breaking each other:

| | Backend session | Frontend session |
|---|---|---|
| Owns | `backend/`, `docs/`, the API, the database | the frontend repository |
| Never edits | the frontend | the backend (any file) |
| Target branch / environment | lands on `develop`, which auto-deploys to **Preview** | builds against **Preview**; `main`/Production is the owner's |

1. **The contract is `openapi.json` on Preview.** Regenerate your types from it at the start of every session and whenever [`api-changes.md`](api-changes.md) has a new entry.
2. **The backend logs every change that touches the contract in `docs/api-changes.md`**: date, endpoints, `additive` or `BREAKING`, and what the frontend should do. Newest first. Additive changes (a new field or endpoint) can be adopted whenever you like; the backend avoids breaking ones, and when unavoidable it adds the new shape first, tells you, and removes the old one only after you confirm.
3. **You tell the backend what you need through `BACKEND_ASKS.md`** in the frontend repository: one entry per ask, with the request you sent, the response you got, and what you expected. The owner carries it over. Do not guess a shape, and do not add a client-side workaround for a backend bug without logging it.
4. **Start of every session:** pull the backend repo; read `docs/api-changes.md` and `git log docs/figma`; run `python docs/figma/snapshot.py --check`; re-fetch `openapi.json`.
5. **The frontend never calls Production** and never creates demo data there. Staging can be reset by the backend session; do not store anything on it you cannot recreate.
6. **No secrets cross the boundary.** Tokens, passwords and keys are set by the owner in each platform's settings or a git-ignored `.env`.

## 7. Suggested first moves

1. Read the settled decisions in section 8; ask the owner only about what it lists as still open.
2. Scaffold the app and the generated API client; read `GET /regions`, `GET /categories` and `GET /products` from Preview to prove the loop works. **CORS is not set for any origin, and with the proxy it never needs to be** (section 7a), so do not call Preview from the browser directly: add a Next.js `rewrites()` entry in `next.config` that forwards `/api/v1/:path*` to `${API_ORIGIN}/api/v1/:path*` (the Preview origin) and have the app call its own origin. That works today, needs nothing from the backend, and can stay in development after CORS is set.
3. Build the **storefront** first, in this order: layout and navigation, landing, category and product listing with filters, product details, cart and checkout, My Account (orders, returns, addresses, wishlist). Compare each against its Figma render at 360, 768, 1024 and 1280 px.
4. Then the seller portal, then the admin portal as its API fills in.
5. Keep a short `DESIGN_NOTES.md` and `BACKEND_ASKS.md` from the first day.

## 7a. Deploying the frontend: two environments that mirror the backend's

The frontend gets its own GitHub repository and its own Vercel project, wired to the backend's environments like this:

| Frontend branch | Frontend site | Talks to | `API_ORIGIN` (server-side env var in the frontend's Vercel project; the origin only, no path) |
|---|---|---|---|
| `develop` | pre-production (Vercel Preview, stable `...-git-develop-...vercel.app` URL) | **backend Preview** (staging data) | `https://asknget-backend-git-develop-aun-syed-shahs-projects.vercel.app` |
| `main` | production | **backend Production** | `https://asknget-backend.vercel.app` |
| your machine | `http://localhost:<port>` | **backend Preview** | the same Preview URL, in the git-ignored `.env.local` |

**The pairing rule: a frontend environment only ever talks to the backend environment next to it in this table.** Production never points at Preview and the other way round, so
staging data and demo accounts can never reach real customers.

- **No CORS is needed, anywhere.** The browser calls the frontend's own origin (`/api/v1/...`); a Next.js `rewrites()` entry (or route handler) forwards it to `API_ORIGIN` on the server (`/api/v1/:path*` to `${API_ORIGIN}/api/v1/:path*`). Keep
  `API_ORIGIN` a plain server variable (no `NEXT_PUBLIC_` prefix) so the backend address is not baked into the browser bundle. Pre-production, production and localhost all work the same way.
- **The proxy changes what the backend sees**: the `Host` is the backend's, so the market is **not** inferred from a `za.` / `pk.` subdomain. **Always send `X-Region`** (and `X-Language`,
  `X-Timezone`, `X-Store`, `X-Cart-Token`; the proxy must forward them, and `Authorization`). The brief's section 3 explains each header.
- **Set up in Vercel** (the owner clicks, the agent tells them what): import the frontend repo as a new project; Production Branch `main`; add `API_ORIGIN` once for *Production* and once for *Preview*
  with the values above (a different value per environment, never "All environments"). No secret is involved: the backend URL is public, and the frontend holds no key.
- **Sharing the pre-production site with the team without Vercel seats**: the same one-time choice as for the backend (Settings, Deployment Protection, turn Vercel Authentication off for that project).
- If the browser ever has to call the backend **directly** (it should not), CORS must list the frontend's origins on the backend: tell the owner, who sets `CORS_ALLOWED_ORIGINS` (and
  `CORS_ALLOWED_ORIGIN_REGEXES` for the changing preview URLs) through the backend session; never edit it yourself.

## 8. Decisions (settled by the owner, 2026-10-16)

| # | Topic | Decision |
|---|---|---|
| 1 | Stack | **Next.js (App Router) + TypeScript + Tailwind**; a typed client generated from `openapi.json`; TanStack Query for server state; Playwright for the screenshots at the four widths. Server rendering for the storefront (SEO). A separate Vite SPA for the portals is allowed if the owner agrees later. |
| 2 | Where it lives | A **separate folder and repository**, worked on by its own session. It never edits the backend, and the backend session never edits it. |
| 3 | Order of work | Storefront, then seller portal, then admin portal (section 7). The agent follows this order. |
| 4 | Mobile | The responsive defaults in `frontend-handoff.md` section 1 (360 / 768 / 1024 / 1280, drawer navigation, cards instead of wide tables, full-screen sheets for popups), built flexibly so a mobile design can replace them. Replace them when the designer delivers mobile frames. |
| 5 | Hosting and CORS | Runs on **localhost** for now. **The port is still to be confirmed by the owner** (Next.js defaults to `3000`); until then the backend session has not added any origin. When the port is confirmed, the backend session adds `http://localhost:<port>` to `CORS_ALLOWED_ORIGINS` on Preview and logs it in `api-changes.md`. |
| 6 | Figma access | The frontend session does **not** call Figma or need a token. It reads pictures from `Figma/frames/` (the owner's export, catalogued; manifest included) and text outlines from `docs/figma/screens/`. The owner re-exports whenever the design changes. |
| 7 | Definition of done | **Screenshots** of each screen at 360, 768, 1024 and 1280 px next to its Figma frame image from `Figma/frames/`. |

Still open: the localhost port (not blocking: use the rewrites proxy above); the two missing frames (**Account Information** and **Recent Activity**, admin; skip those two screens until `Figma/frames/manifest.json` lists them); where the frontend repo keeps the pictures (suggestion: a git-ignored `design/` folder copied from `Figma/frames/`).

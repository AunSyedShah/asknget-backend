# asknget: brief for the agent that builds the frontend

> **Approved by the owner 2026-10-16** (the decisions are recorded in section 8). Written by the backend session so that a fresh Claude
> session can build the frontend against this backend while the backend keeps changing. It is the *entry point*: it says what
> the product is, where everything is, and how the two sessions stay in step. It does not repeat
> [`frontend-handoff.md`](frontend-handoff.md), which holds the API contract and the generated endpoint index; read that second.
> If anything here disagrees with the running API, **the API wins**: `GET <base>/openapi.json` is the contract, `<base>/docs` is its UI.

## 0. Read in this order (about 20 minutes)

1. This file.
2. [`frontend-handoff.md`](frontend-handoff.md) sections 3 to 5b: headers, auth, errors, lists, money, what changed recently.
3. `GET <preview base>/openapi.json` and the Swagger UI. Generate typed clients from it (for example `openapi-typescript`); do not hand-write response types.
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

**Three ways in, from cheapest to dearest. Use them in this order.**

1. **Text outlines, no token and no network.** `docs/figma/screens/<Screen name>__<node id>.txt` in the backend repo: structure, copy, field labels, component states. `docs/figma/manifest.json` lists them all. This is enough to know *what* is on a screen. It has no colours, spacing or images.
2. **Rendered images, needs the token.** `python docs/figma/snapshot.py --render "<frame name or node id>" --scale 1` saves a PNG to `docs/figma/renders/` (git-ignored). Needed for layout, spacing and colour. Use `--scale 0.2` for overviews. This draws on a separate rate-limit budget that has kept working while the others were locked.
3. **A `.fig` export, no token, no rate limit (the owner chose this route).** The owner saves a local copy (File, Save local copy) and, from the backend repo, `uv run --with zstandard python docs/figma/snapshot.py --from-fig <file>.fig` decodes it into the same `docs/figma/screens/*.txt` outlines. It is a point-in-time copy: when the designer changes the file, the owner exports again.
   **It gives structure and copy, not pictures.** The decoder does not export images, colours, fonts or spacing, so it cannot produce the frame images that "compare against the Figma screenshot" needs. Until it does, get frame images one of two ways: (a) the owner exports the frames you are building as PNG from Figma (select the frame, Export, PNG 1x) into a folder you are told about, or (b) `--render` with the token (way 2). Ask the backend session to extend the decoder to also export style tokens (colours, type scale, radii, spacing) from the `.fig`; it needs no API and would remove most of the guessing.

**The token.** `FIGMA_TOKEN` is the owner's personal access token. The tool reads the backend repo's root `.env` (git-ignored), then the environment. **Never print it, paste it into a file, or commit it.** If it returns `401`, the token expired (they last up to 90 days): ask the owner to make a new one and put it in `.env` themselves.

**Rate limits (the file is on a Starter plan).** `--check` is cheap and tells you whether the design changed since the last snapshot. `--index` is one small call. Fetching full frames (`--nodes`, `--only`) is **cost-based: about 10 frames can lock the API for days** (a `429` with "retry after ~N h"). On `429`, **stop**; never retry in a loop. As of 2026-10-16 the text API is locked until about 2026-10-17; renders still work. Two file keys exist: the original (the tool's default) and the owner's duplicate `lWcZqbOFLdHJ2dERb5hZuB` (`--file <key>`), which the handoff recommends for renders.

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
2. Scaffold the app and the generated API client; read `GET /regions`, `GET /categories` and `GET /products` from Preview to prove the loop works (CORS permitting, otherwise through a local proxy).
3. Build the **storefront** first, in this order: layout and navigation, landing, category and product listing with filters, product details, cart and checkout, My Account (orders, returns, addresses, wishlist). Compare each against its Figma render at 360, 768, 1024 and 1280 px.
4. Then the seller portal, then the admin portal as its API fills in.
5. Keep a short `DESIGN_NOTES.md` and `BACKEND_ASKS.md` from the first day.

## 8. Decisions (settled by the owner, 2026-10-16)

| # | Topic | Decision |
|---|---|---|
| 1 | Stack | **Next.js (App Router) + TypeScript + Tailwind**; a typed client generated from `openapi.json`; TanStack Query for server state; Playwright for the screenshots at the four widths. Server rendering for the storefront (SEO). A separate Vite SPA for the portals is allowed if the owner agrees later. |
| 2 | Where it lives | A **separate folder and repository**, worked on by its own session. It never edits the backend, and the backend session never edits it. |
| 3 | Order of work | Storefront, then seller portal, then admin portal (section 7). The agent follows this order. |
| 4 | Mobile | The responsive defaults in `frontend-handoff.md` section 1 (360 / 768 / 1024 / 1280, drawer navigation, cards instead of wide tables, full-screen sheets for popups), built flexibly so a mobile design can replace them. Replace them when the designer delivers mobile frames. |
| 5 | Hosting and CORS | Runs on **localhost** for now. **The port is still to be confirmed by the owner** (Next.js defaults to `3000`); until then the backend session has not added any origin. When the port is confirmed, the backend session adds `http://localhost:<port>` to `CORS_ALLOWED_ORIGINS` on Preview and logs it in `api-changes.md`. |
| 6 | Figma access | The owner exports a **`.fig`** (section 5, way 3). No token is given to the frontend session. |
| 7 | Definition of done | **Screenshots** of each screen at 360, 768, 1024 and 1280 px next to the Figma frame. Because a `.fig` has no images (section 5), the owner also supplies PNG exports of the frames being built, or the backend session renders them. |

Still open: the localhost port; where the `.fig` and the PNG exports are kept (suggestion: a folder in the frontend repo, git-ignored, named `design/`); and whether the backend session should extend the `.fig` decoder to export style tokens (section 5).

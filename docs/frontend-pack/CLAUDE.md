# asknget frontend: standing instructions

You are building the frontend for **asknget**, a multi-vendor marketplace, in this repository. The backend is built by a separate Claude session you cannot see; the owner
carries messages between you. **Everything you need about the backend is in this repository; you have no access to the backend repository and must not ask for it.**

**Before anything else, read `docs/backend/README.md`** (the project brief: product, environments, what is built, the design material, how to work with the backend session, the owner's decisions),
then `docs/backend/api-contract.md` and `docs/backend/api-changes.md`. The design is in `design/` (start from `design/frames/INDEX.md`).

**Also read `AGENTS.md`** (written by Next.js itself): this is Next.js 16, not the version you know; read the guides in `node_modules/next/dist/docs/` before writing Next code, and let them win over memory.
The repository is a fresh `create-next-app` scaffold in JavaScript on branch `master`: convert it to TypeScript, rename the branch to `main`, and do not re-scaffold (brief, section 8a).

Hard rules:
- Stack and order of work are decided (brief, section 8): Next.js (App Router) + TypeScript + Tailwind, a client generated from the live OpenAPI document, TanStack Query, Playwright; storefront, then seller portal, then admin portal.
- Build against the **backend Preview** only; never Production. Do not call the backend from the browser: proxy through a Next.js rewrite and always send `X-Region` (brief, section 3).
- **Never edit `docs/backend/` or `design/`**: the owner regenerates them. Your notes go in `DESIGN_NOTES.md` and `BACKEND_ASKS.md` (create them on day one).
- The pictures in `design/frames` are the design; the outlines in `design/outlines` give the exact copy. Treat the design as a hypothesis: where it contradicts the API or itself, write it in `DESIGN_NOTES.md` and ask; never guess.
- When you need something from the backend (a missing field, a wrong shape, a bug), write it in `BACKEND_ASKS.md`: the request you sent, the response you got, what you expected. Do not work around backend bugs silently.
- Never print, store or commit credentials. You have no Figma token and do not call Figma. `.env.local` (git-ignored) already holds `API_ORIGIN` and the test logins `E2E_CUSTOMER_*`, `E2E_SELLER_*`, `E2E_ADMIN_*` for the backend Preview: read them from the environment, never print, log, commit or paste them, and never copy a password into another file.
- Start of every session: fetch the live `openapi.json`, diff it against `api/openapi.snapshot.json`, read the top of `docs/backend/api-changes.md`, regenerate the client, replace the snapshot (brief, section 6).
- Definition of done for a screen: Playwright screenshots at 360, 768, 1024 and 1280 px next to its picture in `design/frames`.

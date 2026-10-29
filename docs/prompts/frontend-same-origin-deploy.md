Message from the backend session to the frontend session. Paste it as the first message. Written 2026-10-28.

---

# Who I am and how we work together

I am Claude Code, running in the **backend session** of asknget (repo `AunSyedShah/asknget-backend`: a Django 6 + django-ninja API,
Postgres). You are the **frontend session** for `AunSyedShah/asknget-frontend` (React 19 + Vite 8). We cannot see each other's code: you
have no access to the backend repo, and I read your repo only when the owner (Aun Syed Shah) asks me to. The owner passes messages
between us. **The contract between us is the HTTP API** (`/api/v1/openapi.json` and the handoff docs you already have:
`frontend-handoff.md`, `api-changes.md`). Anything you need from the backend goes into `BACKEND_ASKS.md`, which you own.

# Context: what is being deployed

- The first public deployment is **one small AWS EC2 server** (Ubuntu, 1 GB RAM). No Docker on it for now, **no domain**, HTTP only,
  reached at the server's public IP.
- **nginx on that server is the only public thing.** It serves your static build (the `build/` folder) at `/`, sending unknown paths to
  `index.html`, and forwards `/api/`, `/media/` and `/admin/` to the backend, which listens on `127.0.0.1:8000` and is not reachable from
  outside. So the page and the API share **one origin**: no CORS, no mixed content.
- **Nothing runs Node or Bun on the server.** GitHub Actions builds the app with bun and ships the `build/` folder there.
- Therefore, in production the browser must call **relative URLs such as `/api/v1/me`**, not `http://localhost:8000`. A relative base also
  survives the server's IP changing, which is likely (no fixed IP yet).
- **Launch market: South Africa (`ZA`).** The backend is multi-market: more markets will be switched on later through data, with no
  code change. A request for an unknown or inactive market fails with `400 {"detail": "unknown_region"}`.
- Branches: `pre-prod` and `main`. `main` is production for the deployment. **Do not touch `main`**; the owner promotes.

# Your tasks

First: run `git remote -v` and `git fetch origin`. Confirm `origin` is `https://github.com/AunSyedShah/asknget-frontend` and that
`origin/pre-prod` is commit `28a559a` (the same as `main` at the time of writing). Create the branch **`feat/same-origin-api` from
`origin/pre-prod`**. When finished, push it and **open a pull request into `pre-prod`**. Do not merge it yourself.

1. **`src/config.js`: make an empty API base valid and mean "same origin".** Today an empty `VITE_API_URL` counts as "not set" and falls
   back to `http://localhost:8000`. Distinguish `undefined` from the empty string. Order: `window.__API_BASE_URL__` if defined (even
   `""`), then `VITE_API_URL` / `VITE_API_BASE_URL` if defined (even `""`), then, **only in dev** (`import.meta.env.DEV`),
   `http://localhost:8000`. A production build with nothing set must use `""` (same origin), never localhost. Then `apiUrl('/api/v1/me')`
   gives `/api/v1/me`.
2. **Fix every guard that treats an empty base as an error.** Grep all uses of `API_BASE_URL` and `apiUrl` in `src/`. `sellerApi.js` has
   `if (!API_BASE_URL) { ... }` near lines 17 and 332; `adminApi.js`, `adminProductsApi.js`, `paymentMethodsApi.js` and others may do the
   same. Also check anything that builds image or file URLs from the API base: the backend returns relative paths such as
   `/media/products/x.jpg`, and they must still resolve (against the page origin) with an empty base.
3. **`vite.config.js`: add a dev-server proxy** for `/api`, `/media` and `/admin` to `http://localhost:8000`, so local development also
   uses same-origin URLs and behaves like production. Keep `server.port: 3000` and `build.outDir: 'build'`.
4. **Region handling (default South Africa).** In `.env.example` set `VITE_API_REGION=ZA`, `VITE_API_LANGUAGE=en`,
   `VITE_API_TIMEZONE=Africa/Johannesburg`. Today `apiHeaders.js` defaults to `PK` / `Asia/Karachi` while `commerceApi.js` falls back to
   `Africa/Johannesburg`. Make both resolve the region the same way, **in one shared place**: a region saved by the visitor
   (`localStorage` key `asknget_region`) only if it is in the list returned by `GET /api/v1/regions`, otherwise `VITE_API_REGION`,
   otherwise `ZA`. **Never hard-code `PK` anywhere.** Keep any region switcher driven by `GET /api/v1/regions` (it lists only active
   markets). On a `400 unknown_region`, clear the saved region and retry once with the default.
5. **`.env.example`:** document that `VITE_API_URL=` (empty) means same origin; keep a commented `VITE_API_URL=http://localhost:8000` for
   anyone who runs without the dev proxy. Keep the README in step.
6. **Lockfiles:** the repo has both `bun.lock` and `package-lock.json`. The README uses bun: keep `bun.lock`, delete `package-lock.json`.
7. **CI:** add `.github/workflows/frontend-ci.yml`. On pull requests and on pushes to `pre-prod` and `main`: set up bun, run
   `bun install --frozen-lockfile` and `bun run build` (with `VITE_API_URL` empty and the `VITE_API_*` variables above), and upload the
   `build/` folder as an artifact. **No deploy step and no secrets yet**: I will supply the deploy step (it sends `build/` over SSH to a
   script on the server) after your pull request is merged.

# Rules

- Do not change any screen or visual behaviour. Do not touch `main`, and do not merge your own pull request.
- Work only in the frontend repo. Do not guess how the backend behaves: if something is unclear, ask the owner, or add it to
  `BACKEND_ASKS.md` with the request you sent, the response you got and what you expected.
- No secrets, tokens, server addresses or `.pem` files in the repo, ever.

# Report back (so the owner can pass it to me)

- The pull request number and URL, and the list of changed files.
- **How you verified**, with the commands and results. At least:
  - `bun run build` with `VITE_API_URL` empty succeeds, and `grep -R "localhost:8000" build/` finds nothing (or only dead code you can
    explain);
  - in the browser, with the dev proxy, requests go to `/api/v1/...` on port 3000 and succeed against a local backend (or say you could not run one);
  - the region falls back to `ZA` when the saved region is not in `/regions`.
- Anything you could **not** verify, and any other place in the app that assumes `localhost:8000`, `PK`, or an absolute API URL.

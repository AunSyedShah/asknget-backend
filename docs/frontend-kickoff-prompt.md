# Starting the frontend session (fully separated from the backend)

The frontend session gets **no access to this repository**. Everything it needs is generated into its own folder by one command, and that command can be re-run whenever
the backend or the design changes.

## 1. Generate the pack (once, then again after every backend or design update)

From the backend repository, with the new frontend folder's path (it must be OUTSIDE this repository):

```
backend\.venv\Scripts\python docs\make_frontend_pack.py "<path of the new frontend folder>"
```

It writes only inside that folder:

| Written | What | On a re-run |
|---|---|---|
| `CLAUDE.md` | the standing instructions the session reads automatically | **kept** if it exists (never overwritten) |
| `docs/backend/README.md`, `api-contract.md`, `api-changes.md`, `PACK_VERSION.txt` | the project brief, the API contract, the log of backend changes | replaced |
| `design/frames/` (89 pictures: `customer/`, `seller/`, `admin/`, `INDEX.md`, `manifest.json`), `design/outlines/` (82 text outlines), `design/Icons.png` | the design | replaced |

Everything the frontend session creates itself (code, `BACKEND_ASKS.md`, `DESIGN_NOTES.md`, `api/openapi.snapshot.json`) is left alone. The tool scans every generated document for secrets first
and stops if it finds one. There is nothing secret in the pack: no token, no password, no `.env`. The `design/frames` folder is 38 MB; add it to the frontend's `.gitignore` if you do not want it in git.

## 2. First message to paste into the new session

```
Read CLAUDE.md and AGENTS.md, then docs/backend/README.md, docs/backend/api-contract.md and docs/backend/api-changes.md, in that order.
Then do the first task in section 9 of the brief: convert the existing scaffold to TypeScript (do not re-scaffold), set up the API proxy,
generate the API client, and show me GET /regions, GET /categories and GET /products working through the proxy.
After that, ask me which storefront screen to build first.
```

## 3. Keeping the two sessions in step

- The frontend session writes what it needs from the backend into `BACKEND_ASKS.md`; you bring it to the backend session.
- When the backend session says it changed the contract, or the Figma export was updated and catalogued, re-run the command in section 1. The frontend session starts every session by diffing the live OpenAPI document
  against its own snapshot, so it notices changes even if you forget.
- The frontend session never needs to see this repository, and this repository never needs to see that one.

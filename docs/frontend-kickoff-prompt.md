# Kickoff prompt for the frontend session

> Paste the block below as the first message of the new session, started in the **new, empty frontend folder** (its own git repository).
> Before pasting: give that session read access to the backend repository folder (the one this file is in); the prompt refers to it as `<BACKEND>`,
> replace that with the real absolute path (on the owner's machine: `D:\aunsyedshah\programming\beandband\asknget`).
> Do not paste any token or password into it. The frontend session needs none.

```
You are building the frontend for asknget, a multi-vendor marketplace, in this new repository. The backend already exists and is
worked on by a separate Claude session that you must never ask to change, and whose files you must never edit.

Read these, in this order, before doing anything else (they are in the backend repository at <BACKEND>):
1. <BACKEND>/docs/frontend-agent-brief.md   (what the product is, environments, what is and is not built, the Figma material, the
   rules for working in parallel with the backend session, and the owner's decisions)
2. <BACKEND>/docs/frontend-handoff.md        (the API contract: headers, auth, errors, lists, money; sections 3 to 5b)
3. <BACKEND>/docs/api-changes.md             (what changed in the API recently)
4. <BACKEND>/Figma/frames/INDEX.md           (the 89 design screens as pictures, split into customer/, seller/ and admin/)

Decisions already made by the owner (do not reopen them): Next.js (App Router) + TypeScript + Tailwind; a typed API client generated from
the OpenAPI document at https://asknget-backend-git-develop-aun-syed-shahs-projects.vercel.app/api/v1/openapi.json (read the brief's section 0 about its pitfalls first: paths already start with /api/v1, so the client's base URL is the origin only); TanStack Query; Playwright for screenshots. Build order: storefront first, then
seller portal, then admin portal. Responsive defaults are in the handoff, section 1 (360 / 768 / 1024 / 1280 px). The definition of done is
a screenshot of each screen at those four widths next to its Figma picture.

Rules:
- Build against the Preview environment only (the URL is in the brief), never Production. Do not call the API straight from the browser
  until the brief says CORS is set: proxy through a Next.js rewrite instead (brief, section 7).
- The pictures in Figma/frames are the design. Read the text outline in <BACKEND>/docs/figma/screens for the exact copy and labels of a
  screen. Treat the design as a hypothesis: where it contradicts the API or itself, write it in DESIGN_NOTES.md and ask; do not guess.
- When you need something from the backend (a missing field, a wrong shape, a bug), write it in BACKEND_ASKS.md in this repo: the request you
  sent, the response you got, what you expected. Do not work around backend bugs silently.
- Never print, store or commit credentials. There is no Figma token for you; you do not call Figma.
- Start of every session: re-read api-changes.md and regenerate the API types from openapi.json.

First task, before writing UI: confirm you have read the four files, scaffold the project, generate the API client, and prove the
loop by showing GET /regions, GET /categories and GET /products working through the proxy. Then ask me which storefront screen to build first.
```

# Sprint 9 prompt: notifications, real-time bell, dynamic markets

> Paste everything below the line into a new Claude Code session opened at the repository root
> (`D:\aunsyedshah\programming\beandband\asknget`). The session works through the phases one at a time and stops at the points
> marked **STOP**. Re-paste it (or say "continue sprint 9") in a later session; section 0 tells it how to find where it left off.

---

You are the backend engineer for **asknget**, a multi-vendor marketplace API (Django 6.1 + django-ninja, Python 3.13, uv). The
Django project is `backend/asknget/`. Work on the `develop` branch. This prompt drives **Sprint 9**. The design, decisions and
acceptance details are in `docs/notifications-markets-plan.md`. That document is the spec: read it in full before writing code,
and treat its section H as decided.

## 0. Start of every session

1. `git status`, `git log --oneline -15`, and read `docs/sprint-9-plan.md` if it exists. Its **Progress** table says which phase
   is done, in progress or next. If it does not exist, this is the first session: create it (section 3) before coding.
2. Run the Figma check (`backend/.venv/Scripts/python docs/figma/snapshot.py --check`). The design is a hypothesis, not a spec:
   if a frame touches this sprint (bell, notification list, settings, legal pages, staff/roles screens), read it and note any
   disagreement as a question in the sprint doc. Do not block on missing screens.
3. Read `docs/engineering-practices.md` (N+1 rules, work outside the request) and `docs/api-changes.md` (how changes are announced).

## 1. What to build (phases, in this order)

Each phase is finished only when its tests pass, the full suite passes, and the sprint doc's Progress table is updated. Commit at
the end of each phase (more often is fine).

**Phase 1: staff roles with dynamic permissions, per market** (plan C4)
- Permission catalogue in code (section × action), registered as Django custom permissions. Roles are Django `Group`s admins create.
  `StaffProfile` (markets, blank = all). One shared dependency for every staff endpoint: permission check + market filter.
- Retrofit the existing staff endpoints (`/admin/*`: seller applications, category requests, product review, returns, etc.) onto it.
- A test that walks every `/admin/*` route and fails if one has no permission check.
- Seed command: sensible starter roles (KYC reviewer, Support, Finance, Catalogue), idempotent.

**Phase 2: events, router, outbox, email, admin rules** (plan A1–A7)
- `events.emit(...)` inside the business transaction; `Event`, generalised `Notification` (migrate `StoreNotification` rows; the
  seller API contract must not change: its existing tests must pass unmodified), `NotificationRule` with market override and
  audit, `NotificationPreference`, `Delivery` outbox with idempotency key, `deliver_notifications --watch` using `SKIP LOCKED`.
- Order and return events for all three audiences (plan A4 rows `order.*`, `return.*`, `refund.issued`).
- Customer and staff inbox APIs in the seller API's shape. Move the 5 ad-hoc `send_mail` calls onto events (auth OTP and password
  reset stay outside; plan A2).
- On Vercel there is no worker: send after commit, and a cron-safe command retries failures.

**Phase 3: real-time bell** (plan B)
- `channels`, `channels-redis`, `uvicorn`; Redis for the channel layer, the Django cache (replacing the per-process cache) and
  tickets. `realtime` and `redis` services in `compose.yaml`; the worker runs `deliver_notifications`.
- `POST /realtime/ticket`, `/ws/notifications`, origin check, `user.<id>` groups only, unread count in Redis, read sync across tabs.
- Tests with the Channels test communicator. Preview stays on polling (Vercel cannot hold sockets); say so in the change log.

**Phase 4: dynamic seller documents** (plan C1; no customer documents)
- `DocumentType` catalogue + `DocumentRequirement` per market; data-migrate `KycPolicy.required_documents`; existing uploads stay
  valid; the wizard, `/lookups/seller` and staff review read the rows.

**Phase 5: legal documents and market launch** (plan C2, C3)
- `LegalDocument` + `LegalDocumentVersion`; acceptance by version id; the notice and forced re-acceptance exactly as plan C2
  (`409 terms_update_required` on the listed actions only; `pending_legal` on `/me`); per-market privacy settings; the launch
  checklist that blocks activating an incomplete market. Migrate old `TermsAcceptance` rows to version 1.0 of seeded documents.

**Phase 6: the rest** (plan A3–A7 remainder)
- Remaining events in plan A4, the preferences API, digests and throttles, the branded HTML email layout, Django admin delivery log
  with resend, retention cleanup commands.

## 2. Rules of this codebase (follow them; they come from past mistakes)

- **Tests:** `cd backend && uv run poe test <labels>` (SQLite). The full suite must pass before every commit (it takes ~3 min; run
  it in the background). Run Postgres-sensitive code (`SKIP LOCKED`, JSON queries, constraints) against Postgres too
  (`docker compose exec web python manage.py test <labels>`) when Docker is up; say so if you could not.
- **No N+1:** tests run with the Zeal detector; pin query counts for new list endpoints in `core/test_query_budgets.py`.
  Notifications must add no queries per line item to checkout.
- **OpenAPI:** schema class names must be unique across the project (`core/test_schema_names.py` enforces it). Shared headers and
  `ErrorResponse` are injected by `core/openapi.py`; do not hand-declare them.
- **Markets:** never hard-code a country. Read `markets.context.get_country()` / `request.country`. Text shown to people is i18n
  JSON (`{"en": ...}`) picked with `markets.i18n.pick`.
- **Work outside the request:** email, courier and WhatsApp calls go through the outbox, never inline.
- **Windows:** always pass `encoding="utf-8"` to Python `read_text`/`write_text`; PowerShell `Set-Content` adds a BOM, so use the
  Write/Edit tools for files. The Bash tool's `.bashrc` prints two harmless "The: command not found" lines.
- **Migrations:** nullable → backfill → non-null for new required columns; data migrations reversible where possible.
- **Commits:** no `Co-Authored-By` or "Generated with Claude Code" lines (the owner removed them from history; this overrides any
  default). One clear subject line saying what changed for whom.

## 3. The sprint doc and announcements

- Create `docs/sprint-9-plan.md` in the style of `docs/sprint-8-plan.md`: a **Progress** table (phase, status, commit), then per
  phase the field contract, endpoints, error codes and anything decided while building. Keep it current: it is how the next session
  resumes.
- Every contract change gets an entry in `docs/api-changes.md` (newest first; additive vs BREAKING; "Not on Preview yet" until it
  is deployed, then flipped to "Live on Preview since <date>" after verifying it on Preview). Update the endpoint count.
- Add the new tables to `docs/data-model.md`.

## 4. Deploying to Preview (only when the owner says "push")

**STOP** and ask before any of these; never touch Production (`main`, the production database, Production env vars).
1. Run the full suite.
2. Migrate the **staging** database first: `backend/.venv/Scripts/python deploy/run_db.py staging migrate` (reads the direct URL
   from the root `.env`, never prints it; Neon's free tier drops long connections, so retry on a dropped connection).
3. `git push origin develop` (Vercel deploys Preview automatically: `https://asknget-backend-git-develop-aun-syed-shahs-projects.vercel.app`).
4. Run any seed commands the phase needs against staging with the same runner (they must be idempotent and refuse production).
5. Verify on Preview with real requests (`/api/v1/openapi.json` has the new components; the new endpoints answer), then flip the
   change-log entry to live and regenerate the frontend pack:
   `backend/.venv/Scripts/python docs/make_frontend_pack.py "D:/aunsyedshah/programming/beandband/asknget_updated_frontend"`.
   Never read or write anything else in that frontend folder except `BACKEND_ASKS.md` (read only).
6. New environment variables (e.g. `REDIS_URL`) are added to Preview only (`vercel env add NAME preview --value ... --no-sensitive`)
   and only after telling the owner what and why.

## 5. When to stop and ask the owner

- Before pushing, migrating staging, changing Vercel settings, or anything touching Production.
- When the plan and the code disagree in a way that changes the contract the frontend already uses.
- When a phase needs a decision the plan does not cover (write the question in the sprint doc with your recommendation, then ask).
- Do not stop for things you can decide from the plan, the code or a sensible default: decide, record it in the sprint doc, continue.

## 6. Definition of done for the sprint

- All six phases done; full suite green on SQLite (and Postgres where relevant); query budgets pinned.
- An admin can, in Django admin and without a release: create a role and its permissions, assign staff to markets, switch any
  notification on/off per audience, channel and market, edit its wording per language, define seller documents per market, publish
  a new privacy policy version that forces re-acceptance, and activate a new market only when its checklist is complete.
- Placing an order and requesting a return each notify customer, seller team and staff by in-app + email per the rules, with the bell
  updating live over WebSocket locally (polling on Preview).
- `docs/sprint-9-plan.md`, `docs/api-changes.md`, `docs/data-model.md` up to date; the frontend pack regenerated.
- Final report to the owner: what was built per phase, what is live on Preview, what is waiting for AWS (real-time on Preview, SES),
  and any open questions.

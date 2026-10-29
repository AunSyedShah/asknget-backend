# Sprint 9: notifications, real-time bell, dynamic markets

Spec: [`notifications-markets-plan.md`](notifications-markets-plan.md) (decisions in its section H). Driver prompt:
[`prompts/sprint-notifications-markets.md`](prompts/sprint-notifications-markets.md). This file records progress and what was
decided while building; a new session resumes from the **Progress** table.

## Progress

| Phase | Scope | Status | Commit |
|---|---|---|---|
| 1 | Staff roles with dynamic permissions, per market | done, live on Preview 2026-10-20 | see git log "Sprint 9 phase 1" |
| 2 | Events, router, outbox, email, admin rules, inboxes | done, live on Preview 2026-10-20 | see git log "Sprint 9 phases 2-3" |
| 3 | Real-time bell (Channels + Redis) | done; works locally (compose) and on AWS later, not on Vercel | same commit |
| 4 | Dynamic seller documents | done, live on Preview 2026-10-20 | see git log "Sprint 9 phase 4" |
| 5 | Legal documents, re-acceptance, market launch checklist | next (paused by the owner 2026-10-17; design notes below) | |
| 6 | Remaining events, preferences, digests, HTML email, delivery log, retention | | |

Figma re-check 2026-10-17: nine new screens (admin Applications, Application Details, Returns & Refunds, Return Details, Reviews,
Review Detail; customer Leave Review, Order Cancel, Return Order). None is a notification, role or legal screen; the admin ones
belong to the admin portal API (`BACKEND_ASKS` #5). Application Details lists documents: re-read it in phase 4.

## Phase 1: staff roles with dynamic permissions, per market

### Contract

- **Permission catalogue** (`staff/permissions.py`, code-defined because code checks them), registered as Django permissions on
  `staff.StaffProfile` (`staff.<code>`, e.g. `staff.orders.view`). Adding one needs a migration (a test checks they match).
- **Role** = a Django `Group` an admin creates in Django admin and ticks permissions on (the "Staff permissions" are listed under
  the app "staff"). A user's roles are their groups.
- **`StaffProfile`** (one per staff user, optional): `markets` (blank = every market). A superuser has every permission and every
  market.
- **Every `/admin/*` operation** declares its permission: `auth=StaffAuth("orders.view")`. Missing permission: `403
  {"detail": "Missing permission: orders.view"}`. A test walks every `/admin/*` operation and fails if one declares none.
- **Market scope:** lists show only the staff member's markets; a detail outside them is a 404 (it does not exist for them).
  Platform-wide settings (return reasons, return rules for every market) can be changed only by staff with no market limit.
- `GET /admin/me`: `{is_superuser, permissions: [...], markets: [...] | null}` so the admin UI can hide what the person cannot use.
- Migration `staff 0002` creates the starter roles (Administrator with every permission, KYC reviewer, Catalogue, Support,
  Finance) and gives **every existing non-superuser staff account the Administrator role**, so nobody loses access on deploy.

## Phase 2: events, rules, outbox, e-mail, inboxes

### How it works

- Business code calls `notifications.bus.emit("order.placed", order, actor=user, **payload)` inside its transaction. The event
  catalogue (`notifications/catalog.py`) turns the subject into targets (customer / one store's team / one market's staff); the
  rules decide which audiences and channels apply; in-app rows and outbox rows are written in the same transaction; e-mail is sent
  after commit and retried by `deliver_notifications`.
- Events in this phase: `order.placed`, `order.paid`, `order.cancelled` (by customer / seller / staff, whole order or one store's
  part), `order.shipped`, `order.out_for_delivery`, `order.delivered` (whole order or one store's part), `return.requested`,
  `return.cancelled`, `return.approved`, `return.rejected`, `return.no_refund`, `return.escalated`, `return.decided`,
  `refund.issued`. Defaults per audience are in `catalog.DEFAULTS` (plan A4).
- **Rules** (`NotificationRule`, Django admin): per event x audience, optionally per market; resolution market row → every-market
  row → catalogue default. Fields: is_active, in_app, email, realtime (used in phase 3), user_can_opt_out, default_on (used with
  preferences in phase 6), staff_permission, min_amount_cents. Every add/edit/delete in Django admin writes a `RuleChange`
  (who, when, field: [old, new]). The rule form refuses an audience the event has nothing for and unknown staff permissions.
- **Who gets the e-mail:** customer = the account's address, or the order's contact address for a guest (a guest has no bell);
  seller = the owner and every active member whose section access includes the notification's category; staff = active staff
  holding the rule's permission who work in the event's market (or in every market), plus superusers.
- **One e-mail per event per person:** a paid order puts three lines in a seller's bell (new order, payment received, new
  customer) but e-mails only "new order" (`Target.mail = False` for the other two).
- **Outbox:** `Delivery` rows with a unique key (event + channel + address + kind). Sending claims a row with a conditional
  update (`pending` → `sending`), so the after-commit send and any number of `deliver_notifications` workers never send one twice;
  this works on SQLite and Postgres, no `SKIP LOCKED` needed. Failures retry after 1 min, 5 min, 30 min, 2 h (5 attempts), then
  `failed`; a row stuck in `sending` for 15 min goes back to `pending`. Django admin: delivery log with "Send again", event log.
- **Wording:** 22 new template kinds (customer_*, staff_*, refund_issued), seeded by migration `notifications 0008` and editable in
  Django admin; `NotificationTemplate` gained `email_subject` / `email_body` (empty = title / body). Customer subjects keep the
  old format ("asknget order ORD-1 received", "asknget return RET-1: approved").
- **Inboxes:** `/me/notifications` (customer: tabs orders, returns) and `/admin/notifications` (staff: tabs orders, returns;
  lines for the staff member's permissions and markets) have the seller inbox's shape (list, `/summary`, `/read-all`,
  `/{id}/read`). Staff need no permission to read their own bell.
- `StoreNotification` became `Notification` (`RenameModel`, rows kept) with `audience`, `user`, `market`, `permission`, `event`.
  The seller API is unchanged.

### Decided while building

- Events live in the `notifications` app (`Event` model), not a separate `events` app: nothing else needs them yet.
- Not moved to events yet (still direct seller notices, phase 6): stock alerts, payouts, product review, plan changes, category
  requests. Not moved at all: sign-in OTP and password reset (authentication), the team invitation (its link carries a secret
  token, which must not be stored in event or outbox rows), the contact form (it mails the support inbox, not a person).
- Preferences: the model exists; honouring them comes with the preferences API in phase 6.
- On Vercel there is no worker, so e-mail goes right after commit and failures wait for a scheduled `deliver_notifications`
  (not set up on Vercel yet); on Docker/ECS the `notifier` service runs it every 10 s.

## Phase 3: the live bell (WebSocket)

### Contract

1. Signed in, the client calls `POST /api/v1/realtime/ticket` → `{ticket, expires_in: 30, url: "/ws/notifications"}`.
2. It opens `wss://<API host>/ws/notifications?ticket=<ticket>` (`ws://` on http). The ticket works once and for 30 s; the access
   token never goes in a URL. A bad, used or expired ticket: the socket closes with code **4401** (get a new ticket, reconnect).
   The page's `Origin` must be an API host (ALLOWED_HOSTS) or an allowed frontend origin (CORS_ALLOWED_ORIGINS).
3. The server sends `{"type": "hello"}`, then:
   - `{"type": "notification.created", "id", "audience", "category", "kind", "title", "body", "link": {"type", "id"} | null, "unread"}`
     (`audience` customer | seller | staff tells which bell; `unread` is that bell's badge after this one)
   - `{"type": "notification.read", "audience", "unread"}` when the person marks notifications read (every open tab updates).
   The client may send `{"type": "ping"}` → `{"type": "pong"}`; uvicorn also pings every 20 s.
4. The socket is a hint: on connect and reconnect the client calls `/summary` (and the list) to catch up. Nothing is lost while
   disconnected. Where there is no socket (Vercel), poll `/summary` every 30-60 s with the same code path.

### How it is built

- Django Channels + `channels-redis`, served by **uvicorn** in a separate `realtime` service (compose.yaml), nginx routes `/ws/`
  there; gunicorn keeps serving HTTP. Redis (`REDIS_URL`) is the channel layer and the Django cache. Without `REDIS_URL`:
  in-memory layer in development and tests, **no publishing at all** in production-like settings (Vercel), so nothing breaks.
- Messages go only to `user.<id>` groups. Who receives a line is decided when publishing, with the same rules as the inbox
  (customer = the user; seller = owner and members whose section access includes the category; staff = holders of the
  permission in the market), so a store or staff group cannot leak a line to someone who may not see it.
- Publishing happens after the change commits (`bus.emit` → `realtime.published`), skips rules with `realtime` off, and is best
  effort (an error is logged; the database already has the line).
- Tickets are signed (`django.core.signing`, salt `notifications.realtime.ticket`, 30 s) with a nonce; single use is enforced
  with `cache.add` (across processes when the cache is Redis).

### Decided while building

- Badges are counted from the database when pushing (once per person per publish), not kept as Redis counters: simpler, always
  right, and cheap at this size. Revisit if a bell gets very large audiences.

## Phase 4: dynamic seller documents

### Contract

- **`markets.DocumentType`** (Django admin > Markets > Document types): `code` (permanent once created), `name` and `help` per
  language, `accepted_types` (a subset of PDF / JPEG / PNG, the formats the upload code checks safely), `max_mb` (at most the
  platform limit), `default_step`, `is_active` (off = no longer asked for or accepted; earlier uploads stay). Not deletable.
  Migration `markets 0006` creates the seven built-in types.
- **`markets.DocumentRequirement`** (inline on the country): market x account type x document type, `required` (off = offered
  but not needed to submit), `step` (empty = the type's default), `label` and `instructions` per language. An account type with no
  rows in its market uses the platform defaults (`sellers.rules`). Migration `markets 0006` turned each country's KYC JSON
  (document lists and local labels) into rows; `0007` removed the JSON fields from `KycPolicy` (it keeps the identity-field
  labels, the ID-number format and the instructions).
- **API:** `policy.required_documents[]` (application and `/lookups/seller`) now lists required and optional documents, each
  `{type, label, step, required, help, instructions, accepted_types, max_mb}`. `POST /seller/application/documents` takes any
  active catalogue code (`type` is a string, no longer a fixed enum) and checks that type's file types and size
  (`422 "<label> must be PDF"`, `"<label> must be 1 MB or smaller"`, `"Unknown document type: <code>"`). `/lookups/seller`
  `document_types` comes from the catalogue. Staff may ask for any active type in an info request.

### Decided while building

- Requirements apply to applications not yet approved. Approved sellers are not asked again automatically, and "before first
  payout" requirements are **not built**: both need an upload flow for approved sellers and a payout gate. Follow-up, owner's call.
- Document numbers, expiry dates and front/back sides (plan C1) are not built: no screen asks for them yet.
- Market-specific names live on the requirement (`label`), so "CNIC / Passport" (PK) and "SA ID / Passport" (ZA) share the
  type `id_passport`.

## Phase 5: design notes (not started; written before the pause)

- `markets.LegalDocument` (market or every market, `kind` slug: terms, privacy, seller_terms, seller_agreement,
  marketplace_policies, cookies; `audience` customer | seller | all; title i18n) and `LegalDocumentVersion` (version label,
  content i18n Markdown, summary of changes, `effective_at`, `requires_reacceptance`, draft → published; published versions are
  never edited, only superseded). A market's document beats the every-market one of the same kind.
- `accounts.TermsAcceptance` gains a FK to the version and the market; `document` loses its fixed choices (it holds the kind).
- **Only a current version with `requires_reacceptance` blocks anyone** (a v1.0 does not lock out existing customers; a new
  privacy policy marked "must accept again" does). Blocked: customers' checkout, address and payment-method writes; sellers'
  writes under `/seller/*` (StoreAuth and SellerAuth, unsafe methods only). Answer: `409 {"detail": "terms_update_required",
  "pending": [...]}` through an exception handler. `/me` gains `pending_legal`; `POST /me/terms` accepts `version_ids` (old
  `{document, version}` still accepted).
- Public: `GET /legal` (the market's current documents for the footer) and `GET /legal/{kind}` (text in the reader's language).
- Notices: publishing emits `legal.updated` (customer + seller, mandatory, in-app + e-mail; new templates, category `system`,
  which joins the customer bell's tabs); a daily `legal_notices` command emits `legal.effective` on the effective day.
- **No legal text in migrations** (Production would show placeholder text): demo versions come only from `seed_lookups` on
  staging. Old acceptance rows are not linked to versions (nothing to link to in Production yet).
- Country gains `sender_email`, `marketing_opt_in_required`, `data_retention_days`, `minimum_age`, exposed in
  `/regions/current`. Launch checklist (`markets.launch.checklist(country)`): currency, timezone, languages, sender e-mail,
  contact details, current terms + privacy (customer) and seller terms (seller), KYC policy, return policy. It blocks switching a
  market **on** in Django admin only (editing an active market never fails because of it).

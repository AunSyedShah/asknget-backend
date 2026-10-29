# Plan: notifications for every event, real-time bell, and fully dynamic markets

> Status: **approved with the decisions in section H** (2026-10-17). Nothing here is built yet. The sprint is driven by
> [`prompts/sprint-notifications-markets.md`](prompts/sprint-notifications-markets.md).
> Covers three requests: (A) notifications by email + in-app + real-time over WebSocket for customers, sellers and admins, with
> admins switching each notification on or off; (B) a customer bell fed by WebSocket; (C) markets that admins create at will, each
> with its own seller registration documents, its own legal documents (terms, privacy policy) and staff roles with dynamic
> permissions. Customer documents are **not** in scope (section C1).

---

## 0. What exists today (extend, do not rebuild)

| Area | Built | Gap |
|---|---|---|
| Seller in-app notifications | `notifications/`: 17 kinds, admin-editable i18n wording (`NotificationTemplate`, on/off per kind), per-person read state, tabs, unread count, section permissions | Sellers only; no email; no customer or admin inbox; no real-time; on/off is global only (not per audience, channel or market) |
| Email | 5 ad-hoc `send_mail` calls (accounts, orders, returns, team, marketing) | Plain text, sent inside the request, no retry, no log, no preferences, one sender |
| Markets | `markets.Country` (currency, timezone, languages, contact details), `KycPolicy` (required seller documents per account type, labels, ID regex), `ReturnPolicy`, all editable in Django admin; region from subdomain / `X-Region` | Document **types** are a fixed code list (7 values in `SellerDocument.DocumentType`); no customer documents; no legal documents per market |
| Terms | `accounts.TermsAcceptance` (user, document, version, IP) for the four seller checkboxes | Fixed list of four documents, version is a free string, no stored text, not per market, no re-acceptance when a policy changes |
| Real-time | None (no Channels, no ASGI server, no Redis) | Everything in section B |

---

## A. Notifications

### A1. Architecture: one event, many recipients, many channels

```
Business code                      events.emit("order.placed", subject=order, actor=user)
   │                                (inside the same DB transaction as the business change)
   ▼
Event row  ───────────────────────► also the admin "Recent Activity" log (missing Figma screen)
   │
   ▼  Router (NotificationRule table, admin-editable)
   │    who: customer · seller team (filtered by section permission) · staff (filtered by role + market)
   │    which channels: in_app · email · realtime   (later: whatsapp · sms · push)
   ▼
Notification rows (in-app, one per recipient, same transaction: the bell is never ahead of or behind the data)
Delivery rows    (outbox, one per recipient × channel: pending → sent / failed / suppressed)
   │
   ├── after commit: realtime push to Redis → WebSocket (best effort; the database stays the source of truth)
   └── worker: email (retries with backoff, idempotent), later WhatsApp/SMS/push
```

The rule that keeps this simple: **business code only ever calls `events.emit(...)`.** It never decides who is told or how. The
existing `notifications.services.order_paid(...)`-style helpers become thin `emit` calls; the five ad-hoc `send_mail` calls move
onto it.

**Transactional outbox.** The event, the in-app notifications and the delivery rows are written in the business transaction, so
"order placed but nobody told" or "told about an order that rolled back" cannot happen. Sending happens after commit. If a process
dies mid-send, the worker finds the `pending` row and finishes it.

### A2. Admin control (dynamic, no release needed)

New table **`NotificationRule`**, edited in Django admin (and later through the admin portal API, `BACKEND_ASKS` #5):

| Field | Meaning |
|---|---|
| `event` | e.g. `order.placed` (from the event catalogue, A4) |
| `audience` | `customer` · `seller` · `staff` |
| `market` | blank = every market; a market-specific row overrides the global row |
| `in_app` / `email` / `realtime` | on/off per channel |
| `user_can_opt_out` | off = mandatory (order confirmation, refunds, KYC decisions, policy changes) |
| `default_on` | the starting preference for people who never touched their settings |
| `staff_permission` | for `staff`: the permission a staff member must hold to receive it (e.g. `sellers.review_application`), so it follows whatever roles admins define (C4); blank = all staff of that market |
| `throttle` | e.g. "at most once per product per 24 h" (low stock) |
| `digest` | `off` · `daily` · `weekly` (email collected and sent as one) |
| `min_amount` | optional threshold per market (e.g. "high-value order" alert to staff) |

Resolution order: market row → global row → built-in default (in code, so a new event works before anyone edits a row).
Every change is audited (who, when, old → new), because "why did sellers stop getting X" must be answerable.

**Not under admin control on purpose:** sign-in OTP, password reset, email verification. These are authentication, not
notifications; switching them off would lock people out. They stay in `accounts` and use only the shared email layout.

### A3. Per-person preferences

`NotificationPreference(user, category, channel, enabled)`: the person's own switches, within what the admin allows:
- A channel the admin switched off is not shown.
- A mandatory rule (`user_can_opt_out = off`) is shown locked.
- Marketing messages default **off** in markets whose law requires opt-in (POPIA in ZA requires it for direct marketing). This is a
  per-market flag (section C2).
- API: `GET/PUT /me/notification-preferences`. The seller and staff versions are per person, not per store.

Figma has no preferences screen. Until it exists, everyone runs on the admin defaults; the API is ready for the screen.

### A4. Event catalogue (first version)

C = customer, S = seller team, A = staff. **Bold** = mandatory for that audience.

| Event | C | S | A | Notes |
|---|---|---|---|---|
| `order.placed` | **in-app + email** (confirmation) | in-app + email | in-app (high-value only, `min_amount`) | A multi-store order: each store is told only its own items and total |
| `order.paid` / `payment.failed` | in-app + email | in-app | in-app (failure) | |
| `order.cancelled` (by customer / seller / staff) | **in-app + email** | in-app + email | in-app | Wording depends on who cancelled |
| `order.shipped` / `order.out_for_delivery` / `order.delivered` | in-app + email | in-app | n/a | Tracking link when present |
| `return.requested` | **in-app + email** (receipt) | in-app + email | in-app | |
| `return.approved` / `.rejected` / `.escalated` / `.decided` | **in-app + email** | in-app + email | in-app (+ email on escalation) | |
| `refund.issued` | **in-app + email** | in-app | in-app | |
| `review.created` / `review.reported` | n/a | in-app | in-app (reported) | |
| `seller_application.submitted` | n/a | **email** (receipt) | in-app + email (KYC reviewers) | |
| `seller_application.info_requested` / `.approved` / `.rejected` | n/a | **email** | n/a | |
| `product.submitted` / `.approved` / `.rejected` | n/a | in-app + email | in-app (review queue) | |
| `stock.low` / `stock.out` | n/a | in-app (+ daily digest) | n/a | Throttled per product |
| `payout.processed` / `.failed` | n/a | **in-app + email** | in-app (failure) | |
| `category_request.created` / `.decided` | n/a | in-app + email (decision) | in-app (new request) | |
| `team.invited` / `team.role_changed` | n/a | **email** | n/a | |
| `chat.message` | in-app (bell badge only) | in-app (bell badge only) | n/a | Chat keeps its own unread counter; never emailed per message |
| `legal.updated` (section C2) | **in-app + email** | **in-app + email** | n/a | Only to people in the affected market and audience |
| `account.security` (password / email / phone changed) | **email** | **email** | **email** | Always; cannot be switched off |

### A5. Templates and email

- `NotificationTemplate` gains, per event × audience: in-app title/body, email subject, email body (Markdown → HTML, plus a plain
  text version), all i18n (`{"en": ..., "ur": ...}`) with the existing fallback (language → market default → English).
- An optional **market override** of a template (a ZA wording differing from PK).
- One branded email layout: logo, button, footer with the market's contact details from `Country.site_info`, unsubscribe link and
  `List-Unsubscribe` header for opt-out-able email.
- Sender per market (`za@…`, `pk@…`), set on the country.
- Bounces and complaints mark the address suppressed (SES via SNS once on AWS); suppressed addresses get `suppressed` deliveries,
  not retries.
- Emails link to the page; they do not copy sensitive data (no KYC details, no full addresses, no payment data).

### A6. Inbox API (one shape for all three audiences)

| Audience | Endpoints |
|---|---|
| Customer (new) | `GET /me/notifications?tab=&cursor=`, `GET /me/notifications/summary`, `POST /me/notifications/{id}/read`, `POST /me/notifications/read-all` |
| Seller (exists) | `/seller/notifications…`, unchanged contract |
| Staff (new) | `/admin/notifications…`, same shape, filtered by the staff member's markets and roles |

Every item: `id, event, category, title, body, link {type, id}, created_at, read`. The summary returns the unread count per tab.
Cursor pagination (the WebSocket sends the newest id; the client fetches "since").

### A7. Delivery worker

- `python manage.py deliver_notifications --watch`: takes `pending` rows with `SELECT … FOR UPDATE SKIP LOCKED`, so several workers
  never send the same row. Retries 5 times (1 min, 5 min, 30 min, 2 h, 12 h), then `failed`.
- Idempotency key `event_id + recipient + channel` (unique): a retried request cannot send twice.
- Digests: a scheduled run builds one email per person from that period's `digest` deliveries.
- Retention: in-app notifications 90 days, delivery log 30 days, events 1 year (activity log). A scheduled cleanup command.
- Django admin: delivery log (filter by status, channel, event, market), "resend" action, event log.
- **Where it runs:** on ECS it is the existing worker service. On Vercel there is no long-running worker, so Preview sends right
  after commit and a cron call retries failures. The code is the same; only the trigger differs.

---

## B. Real-time bell over WebSocket (customers, sellers, staff)

### B1. Stack

- **Django Channels 4 + `channels-redis`**, served by **uvicorn** (ASGI). Redis is the channel layer (pub/sub between containers),
  the unread-count cache and the store for connection tickets. One Redis for all of it: ElastiCache on AWS, a `redis` service in
  `compose.yaml` locally.
- A **separate `realtime` service** (same image, ASGI command) holds the WebSocket connections, so long-lived sockets never use up
  the HTTP API's gunicorn workers. The ALB sends `/ws/*` to it; everything else goes to the API. Both scale on their own.
- Why not a hosted service (Pusher, Ably, API Gateway WebSockets): Channels keeps everything in our stack and our auth, costs nothing
  per message, and AWS is the target anyway. The publish call sits behind one function (`realtime.publish(user_ids, payload)`), so
  a hosted backend can replace it without touching the event code.

### B2. Connection and security

1. The client (signed in) calls `POST /realtime/ticket` and gets a single-use ticket (random, kept in Redis 30 s).
2. It opens `wss://<host>/ws/notifications?ticket=…`. The server checks and deletes the ticket, then joins the socket to the group
   `user.<id>`. The access token is **never** put in the URL (URLs end up in logs).
3. The origin is checked against the allowed frontend origins. Unauthenticated or expired: close code `4401`; the client fetches a
   new ticket.
4. Heartbeat every 25 s; the ALB idle timeout is raised above it.
5. Guests (not signed in) get no socket.

### B3. What travels on the socket

```json
{"type": "notification.created", "id": "…", "event": "order.shipped", "title": "…", "body": "…",
 "link": {"type": "order", "id": "…"}, "unread": 4}
{"type": "notification.read", "unread": 3}
```

- Only **user** groups. The router has already resolved recipients and permissions (a stock clerk is never in a payouts message),
  so there is no store or staff group to leak through.
- The socket is a **hint**: the database is the source of truth. On (re)connect the client calls the summary and "since cursor",
  so a dropped connection loses nothing.
- Several tabs and devices: all in the same group, all updated; "mark read" in one tab updates the others.
- Unread count: kept in Redis per person (incremented on create, reset on read-all), rebuilt from the database on a cache miss.
- The same socket can later carry chat messages (`type` field), so chat stops polling.

### B4. Environments

| Where | Real-time |
|---|---|
| Local (`docker compose`) | Full: `realtime` + `redis` services added to `compose.yaml` |
| Preview / Production on Vercel | **Not possible** (no long-lived connections). The frontend falls back to polling the summary every 30–60 s with the same API. |
| AWS ECS (planned move) | Full: `realtime` service + ElastiCache |

The frontend should build both: socket when it connects, polling when it does not.

---

## C. Dynamic markets

A market is already a row (`markets.Country`) an admin creates; the request makes everything else about a market data too.

### C1. Seller document requirements

Decision: **customers upload no documents** in any market. The tables below name the audience, so customer documents can be added
later if a market's law ever requires it, but nothing for customers is built in this sprint.

Replace the fixed code list with two tables:

**`DocumentType`** (the catalogue, global): `code`, name (i18n), help text (i18n), accepted file types, max size, "needs a number"
(with label and regex, e.g. CNIC `^\d{5}-\d{7}-\d$`), "needs an expiry date", "front and back", is_active.

**`DocumentRequirement`** (per market): `market`, `audience` (`seller_individual` · `seller_company`; the field is a choice so
`customer` can be added later), `document_type`, `required` (or optional), `when` (`registration` · `before_first_payout`), order,
step in the wizard, instructions (i18n).

- `KycPolicy.required_documents` (JSON) is migrated into rows; the seller wizard, `/lookups/seller?country=` and the staff review
  read the rows. `SellerDocument.document_type` stays a short code, validated against the catalogue, so existing uploads keep
  working.
- An admin can add a market, add a document type ("Tax Clearance Certificate") and require it there, with no release.
- Changing requirements does not invalidate approved sellers automatically; the admin chooses "apply to new registrations" or
  "ask existing sellers too" (which sends them an info request with the new document).
- Documents stay encrypted at rest as today, with access logged.

### C2. Legal documents per market (terms, privacy policy, …)

**`LegalDocument`**: `market` (blank = all), `kind` (`terms` · `privacy` · `seller_agreement` · `marketplace_policies` ·
`cookies` · `returns` · extendable), `audience` (customer / seller / both).
**`LegalDocumentVersion`**: version, content (i18n Markdown), summary of changes, `effective_at`, `requires_reacceptance`, status
(draft → published; published versions are never edited, only superseded).

- `GET /legal` (the market's current documents, for the footer) and `GET /legal/{kind}` (the text in the visitor's language).
- Registration (customer and seller) answers which documents must be accepted; the request sends the **version ids** accepted.
  `TermsAcceptance` gains the version and market (old rows map to "version 1.0" of the seeded documents).
- Decision: **a changed privacy policy (and terms) must be accepted again.** `requires_reacceptance` defaults to on for `privacy`,
  `terms` and `seller_agreement`. Publishing such a version with a future `effective_at` sends `legal.updated` at once (a notice,
  mandatory, in-app + email) and again on the effective day. From `effective_at`, until the person accepts:
  - customers: checkout, saving addresses and payment methods answer `409 terms_update_required` (body lists the versions to accept);
    browsing, the cart and their orders keep working;
  - sellers: every write under `/seller/*` answers the same 409; reading their dashboard, orders and payouts keeps working, so no
    order is stranded;
  - `GET /me` returns `pending_legal: [...]`, so the frontend can show the accept dialog on sign-in.
  `POST /me/terms` accepts by version id (one call for several). Staff are not blocked.
- Per-market privacy settings that the rest of the system reads: marketing opt-in required (A3), data retention days for closed
  accounts and rejected documents, minimum age.

### C3. Market launch checklist

A market cannot be switched to active until it has: currency, timezone, languages, a default sender email, contact details, legal
documents published for every required kind, document requirements for sellers (and customers if required), a return policy, and
at least one delivery method. Django admin shows the checklist on the country page, and activation explains what is missing.

### C4. Staff roles with dynamic permissions, per market

Decision: **roles and their permissions are data**, created by admins, not a fixed list.

- **Permission catalogue** (in code, because each permission is checked by code): section × action, e.g. `sellers.view`,
  `sellers.review_application`, `orders.view`, `orders.cancel`, `returns.decide`, `payouts.manage`, `catalog.review_product`,
  `categories.manage`, `markets.manage`, `legal.publish`, `notifications.manage`, `staff.manage`. Registered as Django custom
  permissions so Django admin honours them too.
- **Role** = a Django `Group` an admin creates in Django admin ("KYC reviewer ZA", "Finance") and ticks permissions on. No release
  needed for a new role or a new combination.
- **`StaffProfile`**: the markets a staff member works in (blank = all), their roles. A superuser sees everything.
- Every staff API and queue checks a permission **and** filters to the staff member's markets (one shared dependency, so a new
  endpoint cannot forget it). Admin notifications go to staff holding the rule's `staff_permission` in the event's market.
- Changes to roles and memberships are audited.
- This is also the base of the admin portal API (`BACKEND_ASKS` #5).

---

## D. Data model summary (new or changed)

| App | Model | Change |
|---|---|---|
| `events` (new) | `Event` | type, actor, market, subject type + id, payload, created_at |
| `notifications` | `Notification` | generalised from `StoreNotification`: recipient user (+ store for seller items), event, params, link, read_at |
| | `NotificationRule`, `NotificationPreference`, `Delivery`, `RuleChange` (audit) | new |
| | `NotificationTemplate` | + audience, email subject/body, market override |
| `markets` | `DocumentType`, `DocumentRequirement`, `LegalDocument`, `LegalDocumentVersion` | new; `Country` + sender email, privacy settings, `is_launch_ready` |
| `accounts` | `TermsAcceptance` + version FK + market; `StaffProfile` (markets, roles = Django groups); custom permission catalogue | new / extended |
| `sellers` | `SellerDocument.document_type` validated against the catalogue | changed (data migration from `KycPolicy`) |

The existing seller notifications API keeps its contract: its rows are migrated into `Notification`.

## E. Infrastructure added

- Dependencies: `channels`, `channels-redis`, `uvicorn[standard]`, `redis` (also used for the Django cache, which fixes the
  per-container cache problem noted in the AWS discussion).
- `compose.yaml`: `redis`, `realtime` (ASGI), `worker` runs `deliver_notifications --watch` next to `embed_products`.
- Settings: `REDIS_URL`, `CHANNEL_LAYERS`, `CACHES`, per-market sender default.
- AWS (with the ECS move): ElastiCache, a `realtime` ECS service, an ALB rule for `/ws/*`, SES with SNS bounce handling.

## F. Phases

| Phase | Scope | Result |
|---|---|---|
| 1 | Staff roles with dynamic permissions + markets (C4): needed first, because admin notifications are routed by permission | Admins create roles; staff APIs filtered by permission and market |
| 2 | Event + router + outbox, `NotificationRule` (admin on/off), customer and staff inbox APIs, email through the worker, order and return events for all three audiences, the 5 ad-hoc emails moved over | Team lead's examples work end to end (bell by polling) |
| 3 | Redis + Channels + `realtime` service + ticket auth + Redis unread counts; locally (and on AWS when it exists) | Real-time bell for all three audiences |
| 4 | Dynamic seller documents (catalogue + requirements, `KycPolicy` migrated) | Admin decides seller documents per market |
| 5 | Legal documents with versions, notice + forced re-acceptance, per-market privacy settings, launch checklist | Admin can launch a new market without a release |
| 6 | The rest of the event catalogue, preferences API, digests, branded HTML email, delivery log and resend, retention cleanup | Complete |

Each phase ships with tests, the change log (`docs/api-changes.md`) and the frontend pack, as before. Phase 2 can run on Preview
only after the AWS move; until then Preview polls.

## G. Testing

- Every event: right recipients per audience and market, permission filtering (a clerk without the payouts section gets no payout
  notification), mandatory vs opt-out, market rule overriding the global rule, throttle and digest.
- Outbox: rollback creates nothing; a retried send is not duplicated; `SKIP LOCKED` with two workers; suppression.
- WebSocket (Channels test communicator): ticket single use and expiry, origin check, only own group, read sync across two sockets.
- Staff: a role's permission change takes effect on the next request; a staff member never sees another market's rows; a staff
  endpoint without a permission check fails a test that walks every `/admin/*` route.
- Documents: market requirement changes, catalogue validation, existing uploads still valid.
- Legal: notice on publish and on the effective day, `409 terms_update_required` on the listed actions only, browsing and seller
  reads still work, re-acceptance clears it.
- Query budgets pinned so notifications do not add N+1 queries to checkout.

## H. Decisions (2026-10-17)

| # | Question | Decision |
|---|---|---|
| 1 | Staff roles | **Dynamic**: admins create roles and choose their permissions (C4); staff are assigned to markets |
| 2 | Customer documents | **None** in any market for now; the model leaves room if a law requires it later (C1) |
| 3 | Privacy policy change | **Notice, and it must be accepted again** before protected actions (C2) |
| 4 | Email languages | Default taken: English wording seeded; every template is already per language, so admins add translations without a release |
| 5 | High-value order alert | Default taken: the rule exists with no amount (off); an admin sets the amount per market to switch it on |
| 6 | Preferences and customer bell screens | Default taken: the API is built; the frontend uses its own layout until Figma has them |
| 7 | Phase order | Staff roles first (notifications route by permission), then F as listed |

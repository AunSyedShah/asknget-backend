# Sprint 9: notifications, real-time bell, dynamic markets

Spec: [`notifications-markets-plan.md`](notifications-markets-plan.md) (decisions in its section H). Driver prompt:
[`prompts/sprint-notifications-markets.md`](prompts/sprint-notifications-markets.md). This file records progress and what was
decided while building; a new session resumes from the **Progress** table.

## Progress

| Phase | Scope | Status | Commit |
|---|---|---|---|
| 1 | Staff roles with dynamic permissions, per market | done (655 tests pass) | see git log "Sprint 9 phase 1" |
| 2 | Events, router, outbox, email, admin rules, inboxes | in progress | |
| 3 | Real-time bell (Channels + Redis) | | |
| 4 | Dynamic seller documents | | |
| 5 | Legal documents, re-acceptance, market launch checklist | | |
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

# Bug fixes after the returns build (plan and progress)

Source: the review of 2026-10-13 (18 findings). Branch: `returns`. Rules for this work:

* **No performance regression.** Read endpoints keep their pinned query budgets (`core/test_query_budgets.py`); any extra query
  goes on a write action (approve, receive, payout), never on a list or detail read. No new indexes on hot tables beyond the one
  partial unique index in phase 4.
* **One phase at a time**: fix, add a test that fails without the fix, run the whole suite, tick the box. If a fix drags in another
  problem, fix that, note it under "Found on the way", and come back to the list.
* **Nothing is pushed** until every phase is done; then SQLite and Postgres (Docker) runs, then one local commit.

## Phase 1 - money and access (bugs 1, 2, 3, 5)

- [x] **1. Return of an order staff already refunded pays twice.** A line is not returnable when its order is `refunded` or its
  store's sale has already gone back in full (`why_not: already_refunded`). Belt and braces: after `ledger.refund_items`, the
  request's `refund_cents` is set to what the ledger actually refunded when a sale exists, so staff can never be told to pay more
  than went back (also closes bug 15's one-cent drift).
- [x] **2. Seller overrules staff.** Seller `approve` / `reject` only from `requested`. `escalated` and `received` are staff-only,
  through `decide`. Enforced in the service by actor, not only in the schema flags.
- [x] **3. Uploads served as web pages.** Return photos and review images: open with Pillow, accept only JPEG / PNG / WebP / GIF by
  the *detected* format, and save under a generated name with the extension of that format (never the client's name). Review videos:
  only mp4 / webm / mov by content type, saved under a generated name with the matching extension. nginx adds
  `X-Content-Type-Options: nosniff` on `/media/`.
- [x] **3b. (found on the way) KYC documents are public.** Seller ID / company documents live under `/media/seller_documents/`,
  which nginx serves to anyone holding the URL. Serve them only through signed, expiring links issued by the API to the applicant
  and to staff; nginx refuses that folder. Same safe-name rule as bug 3; the original name stays in `file_name` for display.
  Product images and avatars get the bug 3 treatment too (same hole).
- [x] **5. Double clicks act twice.** Every return transition re-reads the request with `select_for_update()` inside its transaction
  and checks the status on that fresh row (one extra query per action, writes only).

## Phase 2 - ledger accuracy and the seller's order screens (bugs 4, 6, 14, 15)

- [x] **4. Commission on a partial return uses the average rate.** Use the per-category rates recorded on the fee entry
  (`meta.lines`), with the same voucher ratio `post_sales` used; fall back to pro-rata only for fees without that breakdown.
- [x] **6. Mixed line statuses inside one store.** A store's status is its first line that is still live (not returned or
  cancelled), else its first line. Applied in the seller order list/tabs/KPIs (one SQL expression, no extra query), the order
  detail and `set_store_status`, which now moves only the lines in that current status.
- [x] **14.** The "delivery already refunded" check ignores rejected requests as well as withdrawn ones.
- [x] **15.** Covered by bug 1's ledger-amount sync.

## Phase 3 - courier and e-mail timing (bugs 7, 8)

- [x] **7.** Approval creates the shipment row inside the transaction; the courier is called and buyer e-mails are sent only after
  the transaction commits (`transaction.on_commit`). A rollback can no longer leave a booked pickup or a wrong e-mail behind, and no
  courier call holds a row lock.
- [x] **8.** A webhook update with neither id is ignored; a tracking-number match is used only when exactly one live shipment of that
  courier has it.

## Phase 4 - rules and wording (bugs 9, 10, 11, 13, 16, 17)

- [x] **9.** A *final* rejection blocks the same lines only for the same kind of reason (change of mind vs fault), so a buyer refused
  a change-of-mind return can still claim a defect inside the defect window. Lines the seller already received stay blocked.
  A non-final rejection still blocks (the buyer disputes instead).
- [x] **10.** When staff refuse a damaged parcel, the buyer's e-mail, the timeline and the seller's notification say the seller must
  send the item back to the customer. (A tracked "return to customer" shipment is a later feature: noted, not built.)
- [x] **11.** The order's `return_eligible_until` uses the per-line terms (latest of the change-of-mind and defect windows), from the
  already-prefetched lines: no extra query.
- [x] **13.** A second, partial unique constraint: one all-markets rule per category.
- [x] **16.** Notification params may be per-language maps; the reason and the staff decision are stored that way and rendered in
  the reader's language.
- [x] **17.** The approval e-mail mentions tracking only when there is a label or tracking number; `?reason=` with an unknown code is
  `422`; return admin inlines are read-only (no adding items, events or photos by hand).

## Phase 5 - money elsewhere and the decisions (bugs 12, 18)

- [x] **18. Two payouts at once.** `create_payout` locks the store row before checking the balance.
- [x] **12. Refund after payout.** Decision, not a hold change: holding every sale for the 180-day defect window would starve
  sellers. A late refund may take the balance below zero; payouts are refused while it is (the existing "cannot exceed available"
  rule); the debt is recovered from later sales. Pinned by a test; written down in the data model.

## Finish

- [x] Whole suite on SQLite, then on Postgres in Docker; query budgets unchanged. (543 tests, both green; nginx reloaded and checked: documents 404, media `nosniff`.)
- [x] Docs: data model (rules changed by these fixes), returns plan, frontend handoff (`already_refunded`, seller actions).
- [x] One local commit; no push until asked.

## Found on the way

* 3b, KYC documents publicly served (added to phase 1).
* Product images, avatars and review media had the same client-file-name hole as return photos (folded into bug 3).
* The endpoint index in `docs/frontend-handoff.md` printed every path as `/api/v1/api/v1/...` since sprint 7 (the schema's paths already carry the prefix). Fixed in `docs/gen_endpoint_index.py`.

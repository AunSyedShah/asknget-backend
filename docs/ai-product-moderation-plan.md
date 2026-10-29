# Product moderation: draft plan (text-only, no LLM)

Requested by the team lead (2026-10-25). Today every product a seller submits waits for a person (`pending` → staff
`POST /admin/products/{id}/approve|reject`). With many vendors that queue will not keep up. **Categories stay human-approved**
(unchanged: `catalog/category_requests.py`). This plan covers products only.

**Revision 2026-10-27 (owner):** no vision model for now, **text only**, and **try to do it without an LLM**. Earlier revisions
(DeepSeek API, then self-hosted vision-language model) are replaced. Decisions needed are in section J, suggestions in section N.

> **Flow and lifecycle** (what the seller sees, how the checks run in the background, the gaps found in today's code that must be
> fixed first) are in [`product-review-flow-plan.md`](product-review-flow-plan.md). That plan comes first: its Phases 0-1 build the
> flow with a manual decider, and this plan's checks plug in as the decider.

## A. What changes, in one paragraph

When a seller submits a product, the system checks its **text and numbers** (title, descriptions, tags, benefits, brand, category,
price, barcode, shipping, the seller's history) with three kinds of check that need no LLM and no GPU: **rules** the admins edit,
**small statistical models trained on our own catalogue and our own staff decisions**, and **similarity checks** against what we
have already seen. A product that is clean on all of them and comes from a trusted-enough seller goes live in milliseconds.
Anything doubtful goes to the existing human queue, now sorted by risk and showing *why* it was flagged. Staff can always override,
and one switch returns everything to manual approval. Every staff decision becomes a training label, so the system improves with use.

Because everything runs on CPU inside our own process, **no listing text leaves our servers**. That removes the China-API,
cross-border (POPIA section 72) and third-party-provider questions for this feature entirely.

## B. Principles

1. **The system only buys speed on the easy cases.** Approve needs every check clean; any doubt means a person looks. Auto-reject is
   limited to hard violations and off until the data says it is safe (J-1).
2. **Fail towards the human queue.** Model missing or corrupt, error, kill switch: the product stays `pending`. Never approved
   without a recorded decision.
3. **Rules outrank models.** A rule hit cannot be overridden by a model score.
4. **Explainable by construction.** Linear models and rules can say which words and numbers caused a flag. Reasons shown to staff
   come from codes and weights, not from generated text. Seller-facing messages are templates per reason code.
5. **Honest about blind spots.** Text-only means **photos are not understood** (section C, stage 5, and suggestion N-1). The plan
   compensates with hashes, sampling and limits, and says plainly what it cannot catch.
6. **Roll out in shadow first.** Run and log while humans still decide; compare on real submissions before anything is automatic.
7. **Everything audited:** policy version, model version, inputs hash, scores, reasons, who decided.
8. **Markets differ.** Policy, banned terms, sensitive categories and thresholds are per market, edited by admins, not in code.

## C. The pipeline

```
seller: POST /seller/products/{id}/status  {action: submit}      (unchanged, still validates completeness)
   │  product.status = pending; a ProductCheck row is created in the same transaction
   ▼
0. gates        mode off? kill switch? store in good standing? seller on probation? sensitive category?  → human queue
1. rules        deterministic, per market, editable by admins
2. text model   trained classifier: per-violation risk scores from title/description/tags (+ numbers)
3. fit & novelty does it look like the products we approve, in this category and market? (trained without any violation labels)
4. similarity   duplicates and copy-paste across stores; known-bad listings
5. images       NOT understood: hashes, counts, sizes, duplicates only (sampling compensates)
6. decision     matrix over all signals, trust tier, category sensitivity, market thresholds
   ├─ approve   → portal.products.approve()  (existing function; published_at, category counts, events)
   ├─ review    → stays pending, risk score + reason codes attached for the reviewer
   └─ reject    → portal.products.reject(reason)   (hard violations only, when auto-reject is enabled)
```

Everything is a **`Scorer`** that returns a validated `Signal(name, score, reasons[])`. The decision step only reads signals. Adding
or swapping a scorer later (a better model, an image check, even an LLM second opinion) does not touch the decision logic.

### Stage 0: gates

- **Mode** per market: `off` | `shadow` | `assist` | `auto` (section H).
- **Seller trust tier:** the first N products of a new store (default 5) always go to a person. Stores with upheld takedowns or high
  reject rates stay on manual. Long-standing clean stores can be fast-tracked (checks still run).
- **Sensitive categories** (admin-marked per market, e.g. supplements, medical claims, baby products, anything regulated): always
  human review; the system is advisory.

### Stage 1: rules (explainable, free, instant)

All fields the seller typed are checked after **text normalisation** (Unicode NFKC, case-fold, zero-width and look-alike characters,
leet-speak digits inside words, letters spaced out like `c o p y`, repeated letters), so `fr33`, `CoPy` and `c.o.p.y` match.

| Check | Looks at | Examples |
|---|---|---|
| Banned terms and patterns, per market, with a "why" | all text | "first copy", "master copy", "replica", "AAA", "no prescription", prohibited items per market |
| Off-platform contact | all text | phone numbers, e-mail, WhatsApp/Instagram/Telegram handles, URLs, "call me" |
| Misleading claims | description, benefits | "cures", "100% guaranteed", "FDA approved" with no proof, "permanent results" |
| Price sanity | price, compare-at, variant prices | far below the category median, `compare_at` inflated to fake a discount, one variant 50× another |
| Barcode | barcode | invalid EAN/UPC check digit; the same barcode on listings from unrelated stores (counterfeit hint) |
| Quality and spam | title, description | all caps, keyword stuffing (repeated tokens), empty or copy-paste description, absurd length |
| Shipping and stock sanity | weight, size, stock, handling days | a "150 g" item weighing 90 kg, stock 99,999 at price 1 |
| Category-specific required attributes | option values, benefits/usage | a beauty product with no ingredients or "how to use" (configurable per category) |
| Market rules | country, ships-from | listing not allowed in this market, ships-from nonsense |

Each rule hit is a reason code with a severity (`hard`, `soft`). `hard` always forces review (or reject if enabled for that code).

### Stage 2: trained text classifier (the "model")

- **What it learns:** a risk score for each violation type (counterfeit/copy, prohibited item, misleading claim, contact details,
  spam, abusive language, other) from the product's text and numbers.
- **How:** word and **character** n-gram features (hashing, so spelling tricks, Afrikaans, Zulu, Urdu written in Latin letters and
  mixed-language text all work without a language detector), separate weights per field (title counts more than description), a few
  numeric features (price vs category median, text length, caps ratio, digit ratio, link count). Model: regularised **logistic
  regression** per label, probabilities calibrated on a held-out set.
- **Runtime:** trained offline with scikit-learn, **exported as a small JSON of weights** and scored in plain Python inside Django:
  no scikit-learn, no numpy at scoring time (a small image, and a parity test is easy), milliseconds per product, identical on every environment.
  A test proves the runtime score matches the training library's score on a fixture.
- **Cold start (the honest part):** on day one there are no labelled violations. So this stage starts as **weak**: rules, banned
  terms and a few hundred hand-made examples train a first model that is only advisory in `shadow`. It becomes decisive only after
  real staff decisions have accumulated (J-5 sets the bar). Until then stage 3 does the safe-approval work.

### Stage 3: category fit and novelty (needs no violation labels at all)

These are trained on what we **already have**: approved, active products.

- **Category fit:** a classifier trained on every active product's text with its category as the label. If a listing's text strongly
  predicts a different category (a phone case filed under "Skincare"), it is flagged: mis-categorisation, or a trick to dodge a stricter
  category's review. Needs a reasonable number of products per category; categories with too few are skipped, not guessed.
- **Novelty:** how far this listing is from the products we have approved for this category and market (nearest-neighbour distance in
  a text-embedding space, or, simpler to start, distance in TF-IDF space). **Typical-looking listings are low risk, odd ones go to a
  person.** This is the signal that lets us auto-approve safely before we have any violation labels: "it looks like the hundreds of
  things we already approved, and nothing else flagged it".
- **Embeddings:** the search embeddings (`discovery/embeddings.py`) default to a **lexical hash embedder** (good at near-duplicates,
  blind to meaning), with hosted options that would send text to a third party (not wanted here). For moderation I suggest a small
  **open multilingual sentence-embedding model run locally on CPU** (a few dozen MB, ONNX, tens of ms per product) for novelty and
  category fit; the lexical embedder remains enough for duplicates. Where the model sits (inside the `reviewer` service)
  is in section M.

### Stage 4: similarity

- **Copy-paste farms:** the same title or description (simhash/minhash fingerprint) across several stores, or a store re-listing the
  same thing many times.
- **Known-bad:** near-matches to listings staff previously rejected for a policy reason (keeps catching the seller who re-lists).
- **Seller pattern:** many new listings in minutes with similar text.

### Stage 5: images (no vision model, by decision)

We do not understand photos. What we can do cheaply, in pure Python with Pillow (already installed for `ImageField`):

- count, size, format and minimum resolution; blank or near-blank images; the same image (perceptual hash) used by unrelated stores;
  hashes of images staff previously rejected;
- **sampling**: a **higher** share of auto-approved products (default 20%, falling as confidence grows) is shown to a person afterwards,
  because photos cannot be judged automatically;
- **limits**: in `auto` mode, categories marked `image_risk=high` (e.g. anything where a photo is the main risk) are never auto-approved.

The residual risk (nudity, violence, counterfeit logos, text overlays with contact details inside a photo) is stated in J-3 with options.

### Stage 6: decision matrix (admin-editable thresholds per market)

| Rules | Text model | Fit & novelty | Similarity | Outcome |
|---|---|---|---|---|
| any `hard` hit | any | any | any | review (reject only for the hard-reject list, if enabled) |
| clean | every score below its threshold | fits, not novel | no match | **auto-approve** (if gates allow) |
| clean | any score between thresholds | any | any | review |
| clean | a score above its reject threshold, code on the hard-reject list | any | any | **auto-reject** (if enabled) |
| clean | low | novel, or category mismatch | any | review (looks unlike our catalogue: a person decides) |
| clean | low | fits | known-bad or copy-paste match | review |
| model or data missing, error | | | | review (fail closed) |

A fixed share of auto-approved products (higher than in earlier drafts because photos are not judged) is **also sent to a person
afterwards**. The disagreement rate is the metric that tells us when to loosen or tighten.

## D. After approval: the product is not "done"

- **Edits:** changing title, description, tags, category, or raising the price a lot on an `active` product re-runs the checks.
  The product stays live while it runs (a typo fix must not take a shop offline) and comes down only if the new result says so.
  Cosmetic edits (stock, shipping) do not re-check.
- **`suspended` status (new):** hidden from the storefront like any non-`active` status (storefront queries already filter
  `status='active'`; I will verify cart and checkout also refuse it). The seller sees why and can edit, appeal or resubmit. Placed
  orders are untouched.
- **Policy or model change:** publishing a new policy version or model can queue a rescan of live products (cheap: milliseconds each).
- **Customer reports:** a "Report this product" action feeds the same checks and also catches what text cannot (bad photos).
  Strongly suggested because of the image blind spot (N-1).
- **Appeals:** "Ask for a person to review" puts the product at the front of the human queue with the reasons and the seller's note.
  A human decision on an appeal is final inside the system.

## E. Data model (new, small)

- `moderation.ModerationPolicy` per market and version: `mode`, thresholds per signal, `probation_products`, `sample_rate`,
  `image_sample_rate`, `hard_reject_codes`, `sensitive_category_ids`, `image_risk_high_category_ids`, banned terms and patterns
  (a `BannedTerm` table with the "why" and language tag), required attributes per category, `created_by`, `active`. Edits create a
  new version; old checks keep pointing at the version they ran under.
- `moderation.ProductCheck` (append-only audit, one row per run): product, trigger (`submit|edit|rescan|report|appeal|sample`),
  `content_hash` (unchanged content is not re-judged), policy version, model versions, **signals** (name, score, reason codes,
  top contributing terms), decision (`approved|review|rejected|error`), `decided_by` (`system` or a staff user), timings.
- `moderation.ModerationLabel` (the training set, written when staff decide): product text snapshot, decision, **reason codes**,
  reviewer, whether the system had said the same. Append-only.
- **Staff reject gets structured reasons:** `POST /admin/products/{id}/reject` keeps its free-text `reason` and gains optional
  `codes: [..]` (additive; old callers still work). Without clean codes there is no training data, so this ships in Phase 1.
- `moderation.ModelVersion`: registry of trained artefacts (text classifier, category-fit, novelty reference set): kind, artefact
  file (JSON of weights, small, in our storage), training data range, evaluation results, who activated it, rollback pointer. A
  model becomes active only through this table, and only after it beats the current one on the evaluation set.
- `catalog.Product`: `review_source` (`staff|system`), `reviewed_by`, and a `suspended` value for `ProductStatus`.
- New staff permissions: `moderation.view` (checks, stats), `moderation.manage` (policy, banned terms, mode, kill switch, model
  activation). `products.review` stays the permission to approve, reject, suspend and override. They go into `staff/permissions.py`
  with a migration (a test checks they agree).

## F. Training, evaluation, and keeping it honest

- **Where training runs:** a management command (`train_moderation`) run by a developer or a scheduled job, on a machine that has
  scikit-learn (an optional dependency group, **not** in the runtime image; training runs on a developer machine or a one-off job). It reads `ModerationLabel` rows and approved products,
  trains, evaluates, and writes a `ModelVersion` in `shadow`. Fixed random seed, recorded data range, so a run is reproducible.
- **Evaluation set:** a frozen set of labelled listings (at least 300; the first ones written by the team: violations in the market's
  languages, tricky clean listings, obfuscation attempts). A new model must beat the active one on it: lower false approvals first,
  then fewer needless reviews. Reports per violation type and per market: precision, recall, calibration, and "auto-decided share".
- **Thresholds are chosen on cost, not accuracy:** a false approval (violation goes live) is much worse than a needless review, so
  thresholds are set where false approvals on the evaluation set are at or under the target (J-5).
- **Drift:** weekly job compares score distributions and the share of items going to review against the training period. A jump (new
  product types, a seller campaign, a new language) raises a notice and temporarily lowers the auto-approve share.
- **Adversaries:** sellers will try to evade. The normaliser, char n-grams, known-bad matching and the sampling audit are the defences;
  per-store submission limits stop probing the rules. Rules are never shown to sellers in detail (a reason code and a plain message, not
  the pattern).

## G. Schema validation (pydantic v2, already installed with django-ninja)

Pydantic is used wherever data crosses a boundary, so a bad value is an error rather than a decision:

- `Signal(name, score: confloat(ge=0, le=1), reasons: list[Reason])`, `Reason(code in the policy's catalogue, weight, evidence ≤ 120
  chars)`; `CheckResult` and its `decision`. Unknown codes, scores outside 0-1 and missing fields raise.
- `ModelArtifact`: the exported weights file is validated on load (version, feature spec hash, labels, array lengths, finite numbers).
  A corrupt or mismatched artefact makes the stage return `error`, and the product goes to a person.
- `PolicyDocument`: thresholds in range, every referenced code exists, regexes compile and have a length cap (no catastrophic
  patterns: they run with a time budget), banned-term imports are validated row by row with errors reported back to the admin.
- `TrainingConfig` for the training command. Staff-facing `ai` blocks and seller `review` blocks are ordinary django-ninja schemas.

## H. Rollout (the safety net)

`mode` per market, changed by admins with `moderation.manage`, every change audited:

1. **`off`** (default everywhere): nothing changes.
2. **`shadow`**: all stages run on every submit and are logged; humans approve as today. About two weeks of real submissions give the
   agreement rate and the labelled data.
3. **`assist`**: the admin queue shows the signals, reasons and risk, sorted by risk. Humans still click; we measure acceptance.
4. **`auto`**: auto-approve on, with the sampling audit. Auto-reject stays off until the overturn rate is low enough (J-1).

**Kill switch:** one setting forces every market back to `assist`. Model activation is a separate gate (beat the active model on the
evaluation set, run in shadow first); rollback is one click through `ModelVersion`.

## I. Surface for sellers, admins, frontend

- **Seller pre-check (new, strongly suggested, N-3):** `POST /seller/products/{id}/precheck` runs the **rules only** on the draft and
  returns problems early ("phone numbers are not allowed in descriptions", "add how to use") so the product is clean before it is
  submitted. Same engine, no model, instant. Fewer rejections, shorter queue.
- **Seller:** `status` stays `pending` while a person decides; a clean product turns `active` in the submit request itself. The
  payload gains `review: {state, source: "system"|"staff"|null, message, can_appeal}`. Rejected or suspended products show the reason
  and an appeal button: `POST /seller/products/{id}/appeal`. Notifications use the Sprint 9 events (`product.approved`,
  `product.rejected`, new `product.suspended`) whether a person or the system decided.
- **Admin:** `GET /admin/products` rows gain `ai: {risk, signals[], reasons[], novelty, fit}` and sort by risk. New:
  `GET /admin/products/{id}/checks`, `POST /admin/products/{id}/suspend`, `GET|PUT /admin/moderation/policies/{market}`,
  `GET|POST /admin/moderation/banned-terms` (with CSV import), `GET /admin/moderation/stats` (auto-approve share, override rate,
  sample disagreement, time to live, drift), `GET /admin/moderation/models`, `POST /admin/moderation/models/{id}/activate`,
  `POST /admin/moderation/rescan`. All declare a permission.
- **Staff notifications:** daily digest of flagged and suspended products; a notice when drift or errors rise.
- **Frontend work:** seller wizard pre-check messages, product list states, "ask for review"; admin policy and banned-term editor,
  risk-sorted queue with reason chips, structured reject-reason picker, stats screen. I write the API contract first and hand the
  frontend session the pack.

## J. Decisions needed from the owner (my recommendation first)

1. **May the system reject on its own?** Recommend **no at launch**: auto-approve plus flag-to-human; auto-reject later for a short
   hard list. A wrongly rejected seller costs more trust than a slow approval costs us.
2. **New-seller probation:** the first **5** products of a store always get a human; adjustable per market.
3. **Photos (the blind spot):** (a) **recommended for now:** hashes + a 20% human sample of auto-approvals + no auto-approval in
   `image_risk=high` categories + customer reports; (b) add a small **image-safety classifier** (a compact CNN that flags nudity and
   violence, CPU, not a vision-language model) in Phase 4; (c) no auto-approval at all while images are unassessed (safest, slowest).
4. **Who supplies the policy content?** Prohibited and restricted items per market, banned terms in the languages used, claims that
   need proof, and the first 100 or so example violations come from the owner, staff or counsel; I build the engine and templates.
   Needs a named person per market.
5. **Pass criteria to turn `auto` on** (proposal; without an LLM I set the second number lower than before): on a labelled set of at
   least 300 listings, **false approvals of true violations ≤ 1%** and **≥ 40% of clean listings decided without a person**. Below
   that the system stays in `assist`. We report both numbers after the shadow period and move the bar with evidence.
6. **Take-down of a live product:** `suspended` with a reason and an appeal, never silent deletion.
7. **Customer reviews** also wait for staff today and are plain text. The same engine (toxicity, contact details, spam) fits them
   better than it fits products. Recommend adding reviews in Phase 3 or 4. Categories stay human, as the team lead said.
8. **Privacy wording:** seller terms say listings are checked automatically. Nothing leaves our servers, so this is a short sentence.

## K0. Bulk CSV upload (added 2026-10-27)

Sellers can upload their products as a `.csv`; the checks above run on each product **after** the upload. The full design is in
[`product-csv-import-plan.md`](product-csv-import-plan.md). What it adds here: `trigger=import` on `ProductCheck`, stricter gates for
bulk (probation applies to imported products, a daily auto-approval cap per store, copy-paste detection across the rows of one file),
and grouping of the admin queue by import so staff can review a batch at once. The seller pre-check rules also validate each CSV row.

## K. Phases

| Phase | Scope | Ships behind |
|---|---|---|
| 1 | Models and policy per market, normaliser, **rules engine** (stage 1), banned-term tables with import, `Signal`/`Scorer` interfaces with pydantic schemas, `ProductCheck`, `ModerationLabel`, **structured reject codes**, image hash and heuristics (stage 5), similarity fingerprints (stage 4), new permissions, kill switch, `/admin/moderation/*` read APIs, **seller pre-check** | `mode=off` (changes nothing; pre-check is visible to sellers) |
| 2 | Evaluation set and harness; **category-fit and novelty** (stage 3) trained from the existing catalogue; first text classifier from rules + hand-made examples; `train_moderation` command and `ModelVersion` registry; shadow run on Preview and then Production | `shadow` |
| 3 | Real staff labels → retrained classifier; decision matrix; `assist` queue with reasons; `auto` approve with sampling audit; seller messages, appeal, events; optionally reviews | `assist` → `auto` |
| 4 | After approval: edit re-checks, `suspended`, rescans, customer report, stats and drift screens; optional auto-reject; optional image-safety classifier (J-3b); optional LLM second opinion for the uncertain middle only | per market |

Each phase: tests first, query budgets for the new queue fields, `docs/api-changes.md` entry, Preview migrate and verify, frontend pack regenerated.

## L. Tests that must exist

- Fail-closed: artefact missing, corrupt or mismatched; any scorer raising; kill switch → product stays `pending`, never `active`.
- Rule `hard` hit beats any model score; a novel or mis-categorised listing goes to a person even with low violation scores.
- Normaliser: leet-speak, spaced letters, look-alike Unicode, zero-width characters, repeated letters all still match banned terms.
- Probation, sensitive and `image_risk=high` categories never auto-approve.
- Runtime scorer equals the training library's score on a fixture (parity), to a tight tolerance.
- A model that does not beat the active one on the evaluation set cannot be activated; rollback restores the previous version.
- Labels: every staff approve/reject writes exactly one `ModerationLabel`; callers of reject without codes still work.
- Content hash: unchanged resubmit reuses the last verdict; changed content re-runs. Two workers or a double submit never approve twice.
- Pydantic: out-of-range score, unknown code, bad regex, corrupt artefact each fail cleanly, with a readable error for admins.
- Market scoping: staff see and edit only policies of their markets; policy edits make a new version; old checks keep theirs.
- Regex safety: a catastrophic pattern is rejected at save time and cannot hang a request (time budget).
- Appeals reach the front of the queue; overrides are audited. Every new `/admin/*` operation declares a permission; OpenAPI names are unique.
- Query budgets: the risk-sorted admin queue and the product detail stay within a fixed number of queries (Zeal N+1 check).

## M. Running it where we deploy

- **First deployment: AWS EC2 with the `compose.yaml` stack (owner decision 2026-10-27).** The checks run in a new `reviewer` service
  (`moderate_products --watch --interval 2`, several copies allowed) next to `worker` and `notifier`; see
  [`product-review-flow-plan.md`](product-review-flow-plan.md) section D. A Vercel Preview stays a demo and runs the same code in the request.
- **Rules, text classifier, similarity, image hashes:** pure Python inside Django. Milliseconds. No GPU, no external service, no cost per
  product, nothing leaves our servers. (The pure-Python scorer is kept on EC2 too: it keeps the parity test simple and the image small.)
- **The local sentence-embedding model (stage 3):** with no Vercel bundle limit, the `reviewer` image can include a small open
  multilingual embedding model (a few dozen MB, CPU) **from Phase 2**, so novelty and category fit use real semantic similarity from the
  start. The TF-IDF version remains as the fallback when the model file is missing. The decision matrix treats the two the same.
- **Artefacts:** small JSON files in our storage (`FILE_STORAGE`), loaded once per process and cached; a new `ModelVersion` activates
  by id with no deploy.
- **Settings:** `MODERATION_ENABLED`, `MODERATION_RUN` (`inline|worker`), `MODERATION_ARTIFACT_CACHE_SECONDS`. No keys, no endpoints,
  no allowlist needed because nothing calls out. Documented in `docs/environment.md`.
- Everything else (training, evaluation, drift) is a management command or scheduled job.

## N. Suggestions

1. **Photos are the biggest gap, so plan for it now.** Hashes and sampling help but do not see a nude photo or a fake logo. Combine
   the customer **Report** button, the 20% sample, no auto-approval in image-risky categories, and consider the small image-safety
   classifier in Phase 4. A tiny CNN is not a vision-language model, runs on CPU, and keeps the "nothing leaves our servers" property.
2. **Start with novelty + rules, not with a violation classifier.** With no labelled violations, "looks like the hundreds of
   products we already approved, and no rule fired" is the safest thing we can auto-approve. The supervised classifier takes over
   gradually as staff decisions accumulate.
3. **Prevent instead of detect: the seller pre-check.** Running the rules on the draft while the seller is still in the wizard turns
   most violations into a fix before submit. It is almost free (same engine) and shrinks the queue the most.
4. **Seller reputation is the strongest lever.** A clean history makes a fast-track tier safe; repeated violations route a store back
   to manual. Build the reputation score early (counts of approved, rejected, upheld takedowns, appeals won) even if it only feeds gates at first.
5. **Get local-language terms from local staff**, not from me: Afrikaans, Zulu, Xhosa, Roman Urdu, and the slang sellers use for
   copies and banned goods. A 200-term list per market written by someone who reads the market beats any amount of modelling.
6. **Have counsel confirm the prohibited and restricted list per market** (cosmetics ingredients and claims, supplements, weapons,
   tobacco/vape, counterfeit goods rules). The system enforces what the policy says; it cannot know the law. This is also the first
   thing a regulator or a brand owner asks for.
7. **Make staff faster, not only the system:** bulk approve/reject in the queue, a reason-code picker, "similar to this one" links,
   keyboard shortcuts. Every click then also becomes a clean training label.
8. **Keep the LLM door open but closed.** `Scorer` makes it possible to add an LLM as a second opinion **only for the uncertain
   middle** (a small fraction), self-hosted in `af-south-1` or an approved region. Do this only if, after the shadow period, the
   measured "needless review" share is too high to staff. Until then it is cost and risk with no evidence behind it.
9. **Measure from day one:** time-to-live, share auto-approved, share sent to a person, override rate, sample disagreement, appeals won.
   These are also the numbers you will want to show the team lead.
10. **Add customer reviews to the same engine** (J-7): text-only moderation is a better fit for reviews than for products.

## O. What I verified and what I did not (2026-10-27)

- Read in the code: product statuses and the submit/approve/reject functions (`portal/products.py`), the staff queue, the existing
  embeddings (`discovery/embeddings.py`: default lexical hash embedder; hosted providers optional), staff permissions, the Sprint 9
  event bus.
- Not yet checked: that cart and checkout refuse a non-`active` product (needed for `suspended`); how many real products and categories
  will exist to train category fit and novelty (Production is empty, Preview has demo data only); which small multilingual embedding
  model is best for the worker (to benchmark in Phase 2 on our own data); the legal list of prohibited items per market (owner/counsel).
- The percentages in J-5 and the sampling rates are proposals to be replaced by shadow-period measurements, not predictions.

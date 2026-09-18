"""Generate docs/asknget-backend-developer-guide.pdf.

    cd backend && uv run --with reportlab python ../docs/build_developer_guide.py

Regenerate whenever endpoints or flows change; the PDF is a snapshot of this
script, so edit the content here, not the PDF.
"""

from datetime import date
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (
    KeepTogether,
    PageBreak,
    Paragraph,
    Preformatted,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)
from reportlab.platypus.tableofcontents import TableOfContents

OUT = Path(__file__).parent / "asknget-backend-developer-guide.pdf"
VERSION = "0.5"
TODAY = date.today().isoformat()

# --------------------------------------------------------------------------- #
# Styles
# --------------------------------------------------------------------------- #
INK = colors.HexColor("#1f2937")
MUTED = colors.HexColor("#6b7280")
ACCENT = colors.HexColor("#0f766e")
LIGHT = colors.HexColor("#f3f4f6")
LINE = colors.HexColor("#d1d5db")
CODE_BG = colors.HexColor("#0b1220")
CODE_FG = colors.HexColor("#e5e7eb")
OK = colors.HexColor("#dcfce7")
PLAN = colors.HexColor("#fef3c7")

ss = getSampleStyleSheet()
S = {
    "title": ParagraphStyle("title", parent=ss["Title"], fontSize=26, leading=32, textColor=INK, alignment=TA_LEFT, spaceAfter=6),
    "subtitle": ParagraphStyle("subtitle", parent=ss["Normal"], fontSize=12, leading=16, textColor=MUTED, spaceAfter=18),
    "h1": ParagraphStyle("h1", parent=ss["Heading1"], fontSize=18, leading=22, textColor=ACCENT, spaceBefore=14, spaceAfter=8, keepWithNext=1),
    "h2": ParagraphStyle("h2", parent=ss["Heading2"], fontSize=13.5, leading=17, textColor=INK, spaceBefore=12, spaceAfter=5, keepWithNext=1),
    "h2plain": ParagraphStyle("h2plain", parent=ss["Heading2"], fontSize=13.5, leading=17, textColor=INK, spaceBefore=12, spaceAfter=5, keepWithNext=1),
    "h3": ParagraphStyle("h3", parent=ss["Heading3"], fontSize=11, leading=14, textColor=INK, spaceBefore=8, spaceAfter=3, keepWithNext=1),
    "body": ParagraphStyle("body", parent=ss["Normal"], fontSize=9.5, leading=13.5, textColor=INK, spaceAfter=5),
    "small": ParagraphStyle("small", parent=ss["Normal"], fontSize=8, leading=10.5, textColor=MUTED),
    "cell": ParagraphStyle("cell", parent=ss["Normal"], fontSize=8.3, leading=11, textColor=INK),
    "cellb": ParagraphStyle("cellb", parent=ss["Normal"], fontSize=8.3, leading=11, textColor=INK, fontName="Helvetica-Bold"),
    "mono": ParagraphStyle("mono", parent=ss["Normal"], fontSize=8, leading=10.5, fontName="Courier", textColor=INK),
    "code": ParagraphStyle("code", parent=ss["Code"], fontSize=7.8, leading=10.2, fontName="Courier", textColor=CODE_FG, leftIndent=0),
    "bullet": ParagraphStyle("bullet", parent=ss["Normal"], fontSize=9.5, leading=13.5, textColor=INK, leftIndent=12, bulletIndent=2, spaceAfter=2),
    "note": ParagraphStyle("note", parent=ss["Normal"], fontSize=9, leading=12.5, textColor=INK, backColor=LIGHT,
                           borderPadding=(6, 8, 6, 8), spaceBefore=10, spaceAfter=12),
}


def P(text, style="body"):
    return Paragraph(text, S[style])


def B(text):
    return Paragraph(text, S["bullet"], bulletText="•")


def CODE(text):
    """Dark code block. Preformatted ignores backColor, so paint it via a one-cell table."""
    pre = Preformatted(text.strip("\n"), S["code"])
    t = Table([[pre]], colWidths=[174 * mm], hAlign="LEFT")
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), CODE_BG),
        ("LEFTPADDING", (0, 0), (-1, -1), 8), ("RIGHTPADDING", (0, 0), (-1, -1), 8),
        ("TOPPADDING", (0, 0), (-1, -1), 6), ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
    ]))
    t.spaceBefore = 3
    t.spaceAfter = 8
    return t


def NOTE(text):
    return Paragraph(f"<b>Note.</b> {text}", S["note"])


def table(rows, col_widths, header=True, zebra=True, status_col=None):
    """rows: list of lists of str (already-escaped). First row is the header."""
    data = []
    for r_i, row in enumerate(rows):
        style = "cellb" if (header and r_i == 0) else "cell"
        data.append([Paragraph(c, S["mono"] if (r_i > 0 and isinstance(c, str) and c.startswith("`")) else S[style]) if isinstance(c, str) else c for c in row])
    # strip backtick markers used to request mono font
    for r in data:
        for c in r:
            if isinstance(c, Paragraph) and c.text.startswith("`"):
                c.text = c.text.strip("`")
                c.frags[0].text = c.frags[0].text.strip("`") if c.frags else c.frags
    t = Table(data, colWidths=col_widths, repeatRows=1 if header else 0, hAlign="LEFT")
    st = [
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LINEBELOW", (0, 0), (-1, 0), 0.8, ACCENT if header else LINE),
        ("LINEBELOW", (0, 1), (-1, -1), 0.3, LINE),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ("LEFTPADDING", (0, 0), (-1, -1), 4),
        ("RIGHTPADDING", (0, 0), (-1, -1), 4),
    ]
    if header:
        st.append(("BACKGROUND", (0, 0), (-1, 0), LIGHT))
    if zebra:
        for i in range(1, len(data)):
            if i % 2 == 0:
                st.append(("BACKGROUND", (0, i), (-1, i), colors.HexColor("#fafafa")))
    if status_col is not None:
        for i, row in enumerate(rows[1:], start=1):
            raw = row[status_col]
            if "Implemented" in raw:
                st.append(("BACKGROUND", (status_col, i), (status_col, i), OK))
            elif "Planned" in raw:
                st.append(("BACKGROUND", (status_col, i), (status_col, i), PLAN))
    t.setStyle(TableStyle(st))
    t.spaceAfter = 8
    return t


def flow(steps, widths=(12 * mm, 62 * mm, 58 * mm, 40 * mm)):
    """Sequence table: # | User action / screen | API call | What the backend does."""
    rows = [["#", "Screen / user action", "API call", "Backend behaviour"]] + [[str(i + 1), *s] for i, s in enumerate(steps)]
    return table(rows, widths)


# --------------------------------------------------------------------------- #
# Page furniture
# --------------------------------------------------------------------------- #
def on_page(canvas, doc):
    canvas.saveState()
    canvas.setFont("Helvetica", 7.5)
    canvas.setFillColor(MUTED)
    canvas.drawString(18 * mm, 10 * mm, f"asknget backend - developer guide v{VERSION} - {TODAY}")
    canvas.drawRightString(A4[0] - 18 * mm, 10 * mm, f"Page {doc.page}")
    canvas.setStrokeColor(LINE)
    canvas.line(18 * mm, 14 * mm, A4[0] - 18 * mm, 14 * mm)
    canvas.restoreState()


class Doc(SimpleDocTemplate):
    def afterFlowable(self, flowable):
        if isinstance(flowable, Paragraph) and flowable.style.name in ("h1", "h2"):
            level = 0 if flowable.style.name == "h1" else 1
            text = flowable.getPlainText()
            key = f"toc-{abs(hash(text))}"
            self.canv.bookmarkPage(key)
            self.notify("TOCEntry", (level, text, self.page, key))


# --------------------------------------------------------------------------- #
# Content
# --------------------------------------------------------------------------- #
story = []

# --- Cover ------------------------------------------------------------------
story += [
    Spacer(1, 40 * mm),
    P("asknget Backend", "title"),
    P("Developer Guide for Frontend Engineers and Stakeholders", "subtitle"),
    P(f"Version {VERSION} &nbsp;&nbsp;|&nbsp;&nbsp; {TODAY} &nbsp;&nbsp;|&nbsp;&nbsp; Django 6.1 + django-ninja 1.7 &nbsp;&nbsp;|&nbsp;&nbsp; API base: <font face='Courier'>/api/v1/</font>", "body"),
    Spacer(1, 6 * mm),
    P("This guide explains how the asknget backend is put together, which API to call at each step of the user journeys, "
      "the technologies involved, and how to run it locally with <b>uv</b>. It is written for the frontend team first, "
      "but the overview and flow sections are intended to be readable by product and QA stakeholders too.", "body"),
    Spacer(1, 4 * mm),
    NOTE("The project runs on an agile cadence and the Figma design changes frequently. This document reflects the design "
         "snapshot of 2026-09-11 and the code on branch <font face='Courier'>main</font>. Live, always-current API "
         "documentation is served by the backend itself at <font face='Courier'>/api/v1/docs</font> (Swagger UI). "
         "When this PDF and Swagger disagree, Swagger wins."),
    Spacer(1, 10 * mm),
    P("Contents", "h2plain"),
]
toc = TableOfContents()
toc.levelStyles = [
    ParagraphStyle("toc1", fontSize=10, leading=14, fontName="Helvetica-Bold", textColor=INK, leftIndent=0),
    ParagraphStyle("toc2", fontSize=9, leading=12.5, textColor=INK, leftIndent=14),
]
story += [toc, PageBreak()]

# --- 1. What asknget is ------------------------------------------------------
story += [
    P("1. What the backend serves", "h1"),
    P("asknget is a <b>multi-vendor e-commerce marketplace</b>. The backend exposes one JSON API that serves three audiences:"),
    B("<b>Customers</b> - browse the catalogue, manage cart, checkout, track orders, maintain profile, addresses, saved cards and wishlist."),
    B("<b>Sellers (vendors)</b> - sign up, complete a 5-step KYC application (personal, business, representative, banking, review), "
      "upload documents, respond to reviewer requests, and activate a store once approved."),
    B("<b>Admins</b> - review KYC applications, request more information, approve or reject, and manage catalogue data. "
      "Admin screens are not in the design yet; the Django admin site covers this for now."),
    Spacer(1, 3 * mm),
    P("Delivery status", "h2"),
    table([
        ["Area", "Status", "Notes"],
        ["Data model (38 tables) + migrations", "Implemented", "Derived from all 38 Figma screens; see docs/data-model.md"],
        ["Django admin for every model", "Implemented", "http://127.0.0.1:8000/admin/"],
        ["Auth: register, login, JWT, refresh, logout", "Implemented", "/api/v1/auth/*"],
        ["OTP verification (email, SMS), password change/reset", "Implemented", "SMS provider is a console stub until an SMS gateway is chosen"],
        ["Profile, avatar, addresses, payment methods", "Implemented", "/api/v1/me/*"],
        ["Seller application (5 steps, autosave), documents, submit/resubmit, status timeline, store activation", "Implemented", "/api/v1/seller/*, /api/v1/lookups/*"],
        ["Admin KYC review: queue, request-info, approve/reject, document review", "Implemented", "/api/v1/admin/seller-applications/* (staff JWT)"],
        ["Catalogue: categories, products + filters, PDP, related, reviews, wishlist", "Implemented", "/api/v1/categories, /products/*, /me/wishlist"],
        ["Cart (members + guests), vouchers, delivery methods, checkout", "Implemented", "/api/v1/cart/*, /checkout - order is created pending_payment"],
        ["My Orders, cancel, re-order, public tracking, admin order status", "Implemented", "/api/v1/me/orders/*, /orders/track, /admin/orders/*"],
        ["Banners, newsletter, contact form, site info", "Implemented", "/api/v1/banners, /newsletter/subscribe, /contact, /site-info"],
        ["AI search: hybrid keyword + semantic ranking, typo tolerance, suggestions, query log", "Implemented", "/api/v1/products?q=, /search/suggest - pgvector; offline embedder by default, Voyage/OpenAI by env"],
        ["Recommendations: similar, frequently bought together, trending, 'Recommended for you'", "Implemented", "/api/v1/products/{slug}/similar|frequently-bought-together, /recommendations"],
        ["Docker packaging: API image (uv), Postgres+pgvector, embeddings worker", "Implemented", "compose.yaml (+ compose.override.yaml for live reload)"],
        ["Payment provider: card tokenisation, capture, webhooks, 'save card'", "Planned", "Next sprint. Checkout records the chosen method only; orders stay unpaid until then."],
        ["Seller dashboard: home KPIs, products wizard + review, inventory, orders (per-store fulfilment), customers", "Implemented", "/api/v1/seller/dashboard|products|inventory|orders|customers|search; /admin/products (Figma 2026-09-18)"],
        ["Seller portal: Returns, Chat, Analytics page, Notifications, Team/Managers, social login, invoice PDF", "Not designed", "Sidebar entries only - see data-model.md section 10 Q20-Q34"],
        ["Vendor sub-roles / permissions, premium tier, vendor-customer chat", "Not designed", "'Team/Managers' and 'Chat' now appear in the seller sidebar; still no screens"],
    ], (62 * mm, 24 * mm, 86 * mm), status_col=1),
]

# --- 2. Technology -----------------------------------------------------------
story += [
    P("2. Technology stack", "h1"),
    table([
        ["Layer", "Technology", "Why / notes"],
        ["Language", "Python 3.13", "Pinned in backend/.python-version"],
        ["Package / env manager", "uv 0.12", "Replaces pip + venv; lockfile uv.lock guarantees identical installs"],
        ["Web framework", "Django 6.1", "ORM, migrations, admin, auth primitives"],
        ["API framework", "django-ninja 1.7", "Fast, typed REST on top of Django; pydantic v2 validation; auto OpenAPI/Swagger"],
        ["Validation", "pydantic 2", "Every request body is a typed schema; errors are returned as 422 with field paths"],
        ["Auth", "PyJWT (HS256)", "Short-lived access tokens; opaque rotating refresh tokens stored hashed"],
        ["Database", "PostgreSQL 17 + pgvector (target) / SQLite (dev fallback)", "Set DATABASE_URL to switch; psycopg 3 driver with connection pool; pgvector for embeddings, full-text + trigram for keywords"],
        ["Embeddings", "hash (offline, default) / Voyage AI / OpenAI", "EMBEDDING_PROVIDER; one vector per product (discovery.ProductEmbedding); swap provider then embed_products --all"],
        ["Containers", "Docker Compose: db, web (gunicorn), worker", "Two-stage image from astral/uv; dev override bind-mounts the code for live reload"],
        ["Field encryption", "cryptography (Fernet)", "ID/passport numbers and bank account numbers encrypted at rest"],
        ["Images", "Pillow", "Avatar, product, banner, store images"],
        ["Config", "python-dotenv + dj-database-url", "All settings via environment variables / backend/.env"],
        ["Docs", "Swagger UI (built in)", "/api/v1/docs and /api/v1/openapi.json"],
        ["Tests", "Django TestCase", "End-to-end through the real URL config; 121 tests, green on SQLite and on Postgres+pgvector inside the container"],
    ], (34 * mm, 52 * mm, 86 * mm)),
]

# --- 3. Architecture ---------------------------------------------------------
story += [
    P("3. Architecture", "h1"),
    P("Repository layout", "h2"),
    CODE("""
asknget/                     git root  (github.com/AunSyedShah/asknget)
+-- docs/
|   +-- data-model.md        every table, field, enum, relationship, open questions
|   +-- figma/snapshot.py    dumps the Figma screens as text; --check detects design drift
|   +-- build_developer_guide.py   generates this PDF
+-- backend/
    +-- pyproject.toml, uv.lock, .env.example, README.md
    +-- asknget/             Django project root  (manage.py lives here)
        +-- asknget/         settings.py, urls.py, api.py (NinjaAPI root, error handlers)
        +-- core/            shared: base models, EncryptedTextField, validators, JWT auth, seed command
        +-- accounts/        User, OTP, refresh tokens, terms audit, addresses, payment methods
        +-- catalog/         categories, brands, products, variants/options, reviews, wishlist
        +-- sellers/         seller application (KYC), documents, info requests, stores
        +-- orders/          cart, vouchers, delivery methods, orders, status timeline, payments
        +-- marketing/       banners, newsletter, contact messages
"""),
    P("The three-file pattern inside every app", "h2"),
    P("Each Django app is split the same way so that a change lands in exactly one place. This is the main reason the codebase "
      "tolerates frequent design changes:"),
    table([
        ["File", "Owns", "Changes when..."],
        ["schemas.py", "Request/response shapes and field-level validation (pydantic)", "a form on the design changes"],
        ["services.py", "Business rules: uniqueness, cooldowns, state machines, side effects, transactions", "a flow or rule changes"],
        ["api.py", "Routers - thin functions: parse, call a service, return", "an endpoint is added or renamed"],
        ["models.py", "Database tables and constraints", "the data model changes (needs a migration)"],
        ["tests.py", "End-to-end tests through the real URLs", "any of the above changes"],
    ], (26 * mm, 92 * mm, 54 * mm)),
    P("Request lifecycle", "h2"),
    CODE("""
Frontend  --HTTP JSON-->  Django URLconf (/api/v1/)  -->  NinjaAPI
                                                          |
                              1. Auth class (JWTAuth) validates Bearer token -> request.auth = User
                              2. pydantic schema validates + normalises the body (422 on failure)
                              3. Router function calls a service
                              4. Service enforces rules, touches the ORM inside a transaction
                              5. Response schema serialises the result (explicit field list only)
                                                          |
Frontend  <--JSON + status code (200/201/204 or 4xx {"detail": ...})--
"""),
    P("Design principles the frontend can rely on", "h2"),
    B("<b>Explicit responses.</b> Every response schema lists its fields; nothing leaks by accident (no password hashes, no internal tokens)."),
    B("<b>Separate In / PatchIn / Out schemas.</b> Create payloads require their fields; PATCH payloads are fully optional and <i>omitted is not null</i> - only keys you send are updated."),
    B("<b>Enums are shared.</b> Dropdown values (address type, gender, order status, application status...) come from one source and appear in Swagger."),
    B("<b>Rules live server-side.</b> The frontend should validate for UX, but the API is the authority (cooldowns, uniqueness, default switching, state transitions)."),
    B("<b>Versioned base path.</b> Everything is under <font face='Courier'>/api/v1/</font>. A breaking change means a new version, not an edit."),
]

# --- 4. Running locally ------------------------------------------------------
story += [
    PageBreak(),
    P("4. Running the backend locally with uv", "h1"),
    P("Prerequisites", "h2"),
    B("Git, and <b>uv</b> (https://docs.astral.sh/uv/). uv installs the pinned Python 3.13 for you; no system Python required."),
    B("Optional: PostgreSQL 15+ if you want parity with production. Without it the project falls back to SQLite automatically."),
    P("First-time setup", "h2"),
    CODE("""
git clone https://github.com/AunSyedShah/asknget.git
cd asknget/backend

uv sync                                  # creates .venv and installs exactly what uv.lock says
cp .env.example .env                     # then edit .env (see below)

cd asknget                               # the Django project root (contains manage.py)
uv run python manage.py migrate          # create the database schema
uv run python manage.py seed_lookups     # categories, brands, banks, delivery methods
uv run python manage.py seed_demo        # demo accounts, store, 13 products, vouchers (dev only)
uv run python manage.py createsuperuser  # optional: your own admin login
uv run python manage.py runserver        # http://127.0.0.1:8000
"""),
    P("Environment variables (backend/.env)", "h2"),
    table([
        ["Variable", "Required", "Purpose"],
        ["SECRET_KEY", "prod", "Django secret; also signs access tokens. A dev default exists but never use it beyond localhost."],
        ["DEBUG", "no", "1 (default) or 0"],
        ["ALLOWED_HOSTS", "prod", "Comma-separated hostnames"],
        ["DATABASE_URL", "no", "postgres://user:pass@host:5432/asknget - omit to use SQLite (backend/asknget/db.sqlite3)"],
        ["FIELD_ENCRYPTION_KEY", "yes*", "Fernet key for encrypted PII. *Required before any seller banking/ID data is saved. "
                                         "Generate: python -c \"from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())\""],
        ["JWT_ACCESS_TTL_MINUTES", "no", "Default 15"],
        ["JWT_REFRESH_TTL_DAYS", "no", "Default 30"],
        ["OTP_TTL_MINUTES / OTP_RESEND_COOLDOWN_SECONDS / OTP_MAX_ATTEMPTS", "no", "Defaults 10 / 45 / 5"],
    ], (48 * mm, 16 * mm, 108 * mm)),
    P("Useful URLs once running", "h2"),
    table([
        ["URL", "What"],
        ["http://127.0.0.1:8000/api/v1/docs", "Swagger UI - try every endpoint in the browser (use Authorize with a Bearer token)"],
        ["http://127.0.0.1:8000/api/v1/openapi.json", "OpenAPI 3 spec - feed it to a client generator (openapi-typescript, orval, etc.)"],
        ["http://127.0.0.1:8000/api/v1/health", "Liveness probe"],
        ["http://127.0.0.1:8000/admin/", "Django admin - inspect and edit any table"],
    ], (62 * mm, 110 * mm)),
    P("Everyday commands", "h2"),
    CODE("""
uv run python manage.py test                 # run all tests
uv run python manage.py test accounts        # one app
uv run python manage.py makemigrations       # after changing models
uv run python manage.py migrate
uv add <package>                             # add a dependency (updates pyproject + uv.lock)
python ../docs/figma/snapshot.py --check     # has the Figma design changed since we last looked?
"""),
    P("Demo accounts (after seed_demo; password DemoPass123!)", "h2"),
    table([
        ["Email", "Role", "Use it for"],
        ["customer@asknget.demo", "Customer", "Browse, cart, checkout, My Orders, wishlist (has a saved address)"],
        ["seller@asknget.demo", "Active seller", "Seller portal with an approved, active store ('Huda Beauty') and 13 products"],
        ["admin@asknget.demo", "Staff / superuser", "Django admin and every /api/v1/admin/* endpoint (KYC review, order status)"],
    ], (44 * mm, 28 * mm, 100 * mm)),
    P("Vouchers seeded: <font face='Courier'>SAVE20</font> (20% over $50) and <font face='Courier'>WELCOME5</font> ($5, once per user)."),
    NOTE("OTP emails are printed to the runserver console in development (console email backend); SMS codes are logged. "
         "Look at the terminal running <font face='Courier'>runserver</font> to find the 6-digit code while testing."),
]

# --- 5. Authentication -------------------------------------------------------
story += [
    PageBreak(),
    P("5. Authentication and error handling", "h1"),
    P("Tokens", "h2"),
    table([
        ["Token", "Lifetime", "Where to keep it", "How it is used"],
        ["access_token (JWT)", "15 min", "Memory (not localStorage if you can avoid it)", "Header on every protected call: Authorization: Bearer &lt;access_token&gt;"],
        ["refresh_token (opaque)", "30 days", "Secure httpOnly cookie or secure storage", "POST /auth/refresh to get a new pair. It is single-use: every refresh returns a new refresh token and the old one is dead."],
    ], (34 * mm, 16 * mm, 50 * mm, 72 * mm)),
    P("Session lifecycle the frontend must implement", "h2"),
    flow([
        ["App start", "POST /auth/refresh {refresh_token}", "Rotates the token; 401 means the user must log in again"],
        ["Any protected call returns 401 'Access token expired'", "POST /auth/refresh, then retry the original call once", "Standard silent-refresh interceptor"],
        ["User presses Logout (popup)", "POST /auth/logout {refresh_token} or {all_devices: true}", "Revokes the refresh token(s). Discard both tokens client-side."],
        ["Password changed or reset", "-", "All refresh tokens are revoked server-side; other devices are signed out on their next refresh"],
    ]),
    P("Status codes and error shape", "h2"),
    table([
        ["Code", "Meaning", "Body"],
        ["200 / 201 / 204", "OK / created / no content", "Resource, or empty for 204"],
        ["400", "Business rule failed on user input (wrong OTP, wrong current password)", "{\"detail\": \"...\"}"],
        ["401", "Missing/invalid/expired token, or bad credentials", "{\"detail\": \"...\"}"],
        ["403", "Account suspended / not allowed", "{\"detail\": \"...\"}"],
        ["404", "Resource not found or not yours", "{\"detail\": \"Not found\"}"],
        ["409", "Conflict - email or phone already registered", "{\"detail\": \"...\"}"],
        ["422", "Validation failed", "{\"detail\": \"summary\", \"errors\": [{\"field\": \"phone\", \"message\": \"...\"}]}"],
        ["429", "Rate limited - OTP resend cooldown or too many attempts", "{\"detail\": \"Please wait 32s ...\"}"],
    ], (26 * mm, 74 * mm, 72 * mm)),
    P("Example 422 body:"),
    CODE("""
{
  "detail": "phone: Phone must be in international format, e.g. +923001234567;
             confirm_password: Passwords do not match",
  "errors": [
    {"field": "phone", "message": "Phone must be in international format, e.g. +923001234567"},
    {"field": "confirm_password", "message": "Passwords do not match"}
  ]
}
"""),
    P("Use <font face='Courier'>errors[].field</font> to highlight inputs and <font face='Courier'>detail</font> as a fallback toast. "
      "A field of <font face='Courier'>null</font> means the error concerns the whole payload."),
]

# --- 6. Flows ----------------------------------------------------------------
story += [
    PageBreak(),
    P("6. API flows by user journey", "h1"),
    P("Each table reads top to bottom: what the user does on the screen, which API the frontend calls, and what the backend does. "
      "Every endpoint below is live in Swagger except the payment-provider steps, which are marked <i>next sprint</i>."),

    P("6.1 Customer sign-up and verification", "h2"),
    flow([
        ["Sign-up form submitted", "POST /auth/register {email, password, confirm_password, phone?, full_name?}",
         "Creates the user, returns user + tokens (201). Email lower-cased, phone normalised to E.164. 409 if taken."],
        ["'Verify Your Email' screen shown", "POST /auth/otp/send {channel: 'email', purpose: 'signup_email'} (Bearer)",
         "Sends 6-digit code; response has target_masked, expires_at, resend_available_at (45 s cooldown)"],
        ["User types the 6 digits", "POST /auth/otp/verify {channel, purpose, target: email, code}",
         "Marks email_verified_at. 400 wrong code, 429 after 5 wrong attempts."],
        ["'Resend code in 00:45' countdown ends", "POST /auth/otp/send again", "429 with remaining seconds if called too early"],
        ["Mobile OTP screen (same pattern)", "channel: 'sms', purpose: 'signup_phone'", "Sets phone_verified_at"],
    ]),
    NOTE("Registration already returns tokens, so the user is logged in immediately; there is no need to call /auth/login after sign-up. "
         "The <font face='Courier'>user</font> object in the response is the same shape as <font face='Courier'>GET /me</font>, with "
         "profile fields empty until the user fills them in on the Personal Information page."),

    P("6.2 Login", "h2"),
    flow([
        ["Sign-in form", "POST /auth/login {email, password}", "200 with user + tokens; 401 bad credentials; 403 suspended"],
        ["'Forgot password'", "POST /auth/password/forgot {email}", "Always 200 (does not reveal whether the email exists); emails a code if it does"],
        ["Reset form", "POST /auth/password/reset {email, code, new_password, confirm_password}", "Sets password, revokes all sessions. User must sign in again."],
    ]),

    P("6.3 My Account - Personal Information", "h2"),
    flow([
        ["Page opens", "GET /me", "Full profile incl. email_verified, phone_verified, member_since (Account Information panel)"],
        ["'Save Changes' on Personal Details", "PATCH /me {full_name?, gender?, date_of_birth?, profession?, country?, city?, address_line?}",
         "Partial update - only sent keys change. Enums and past-date checks return 422."],
        ["'Change Photo'", "POST /me/avatar (multipart, field name 'file')", "PNG/JPG/GIF, max 2 MB; returns updated profile with avatar_url"],
        ["Change email or phone", "OTP flow (6.1) - not PATCH /me", "Email/phone are identity fields and require re-verification (endpoint planned)"],
    ]),

    P("6.4 My Account - Password Manager and Logout", "h2"),
    flow([
        ["'Update Password'", "POST /me/password {current_password, new_password, confirm_password}", "400 if current is wrong; on success all other sessions are signed out"],
        ["Logout popup confirmed", "POST /auth/logout {refresh_token}", "204; clear tokens client-side"],
    ]),

    P("6.5 My Account - Manage Addresses", "h2"),
    flow([
        ["Tab opens", "GET /me/addresses", "List, default first"],
        ["'Save Address' in popup", "POST /me/addresses {type, label?, full_name, phone, street_address, unit_floor?, city, province?, postal_code, country?, delivery_instructions?, is_default?}",
         "201. The first address always becomes default; is_default=true moves the default."],
        ["'Edit' on a card", "PATCH /me/addresses/{id}", "Partial update"],
        ["Set as default", "POST /me/addresses/{id}/default", "Exactly one default is enforced by a DB constraint"],
        ["Delete", "DELETE /me/addresses/{id}", "204; soft-deleted; if it was default, the newest remaining address becomes default"],
    ]),

    P("6.6 My Account - Payment Methods", "h2"),
    NOTE("Card numbers and CVV never reach this API. The frontend tokenises the card with the payment provider's SDK and sends the "
         "token plus display data. The provider is not chosen yet; the contract below is provider-agnostic."),
    flow([
        ["Tab opens", "GET /me/payment-methods", "List with display_name like 'VISA ....2389'"],
        ["'Add Card' in popup", "POST /me/payment-methods {type: 'card', provider, provider_token, card_brand, card_holder_name, last4, exp_month, exp_year, is_default?}",
         "201. For type=card all card fields are required and expiry must be in the future. PayPal/Apple/Google Pay need only type, provider, provider_token."],
        ["Set default / delete", "POST .../{id}/default, DELETE .../{id}", "Same semantics as addresses"],
    ]),

    P("6.7 Seller sign-up and KYC application", "h2"),
    flow([
        ["'Create your seller account' (account type radio + form + terms checkbox)", "POST /auth/seller/register {email, phone, password, confirm_password, account_type, accept_terms: true}",
         "Creates user (is_seller) + draft application + terms audit rows; returns tokens. 422 if accept_terms is false."],
        ["Email and mobile OTP screens", "Same as 6.1", ""],
        ["Wizard opens / resumes", "GET /seller/application", "Everything: current_step, per-step complete/missing_fields/missing_documents, all saved values (ID and account numbers masked), documents, can_submit"],
        ["Dropdowns (business type, role, account type, bank)", "GET /lookups/seller, GET /lookups/banks?country=PK, GET /categories", "Public; option lists live in one place on the backend"],
        ["Step 1-4 'Save & Continue' (also on blur / autosave)", "PATCH /seller/application/steps/basic-info | business-info | representative | banking",
         "Partial: send only the fields on screen; nothing is required until submit. Returns the full application so the stepper can re-render. 409 once submitted."],
        ["'+ Upload Document' (ID, company registration, bank letter, proof of address)", "POST /seller/application/documents (multipart: type, file)",
         "PDF/JPG/PNG up to 10 MB. Re-uploading the same type creates version n+1; old version kept for reviewers."],
        ["'+ Add Another Director'", "POST /seller/application/directors, PATCH|DELETE .../directors/{id}", "Up to 10"],
        ["Step 5 'Submit'", "POST /seller/application/submit {confirm_accuracy: true, accept_terms: true}",
         "422 lists exactly what is missing per step; otherwise draft -> submitted, application number ASK-SLR-xxxxxx issued, returns the status payload"],
        ["Application Submitted / Status page", "GET /seller/application/status",
         "application_number, status, status_headline, 5-stage timeline (done | current | pending | skipped), events, open_info_request"],
        ["'We Need More Information' banner + 'Required Updates' list", "GET /seller/application (open_info_request.items[])",
         "Each item is a document to replace or a field group to edit; `resolved` flips as the seller acts"],
        ["'Update & Resubmit'", "POST .../documents (same type), PATCH the step, then POST /seller/application/resubmit {confirm_accuracy: true}",
         "422 until every item is resolved; then more_info_required -> resubmitted and the application locks again"],
        ["'Activate Your Seller Account' - Store URL check", "GET /seller/store/slug-available?slug=huda-beauty", "Returns available + a free suggestion"],
        ["'Activate & Start Selling'", "POST /seller/store/activate {display_name?, slug?, accept_seller_agreement: true, confirm_info_accurate: true}",
         "Only when approved; store becomes active at asknget.com/store/&lt;slug&gt;; application -> active"],
    ]),
    NOTE("Per account type: <b>individual</b> sellers skip the Representative step and the company-registration document; "
         "sole proprietors and registered companies complete all four steps and four documents. This lives in sellers/rules.py and is a one-line change."),

    P("6.8 Admin: KYC review (no screens designed - Swagger or a simple admin UI)", "h2"),
    flow([
        ["Review queue", "GET /admin/seller-applications?status=submitted&q=&page=", "Staff JWT. Drafts are excluded; oldest submission first"],
        ["Open an application", "GET /admin/seller-applications/{id}", "Full detail incl. decrypted ID / account numbers, every document version, info requests, event log"],
        ["Start review", "POST .../{id}/start-review", "submitted -> under_review; records reviewer"],
        ["Ask for more information", "POST .../{id}/request-info {message, items: [{kind: document|field_group, target, instruction}]}",
         "-> more_info_required; flags documents replacement_requested; seller sees the banner"],
        ["Accept / reject a single document", "POST .../{id}/documents/{docId}/review {status, note}", "Does not change application status"],
        ["Approve", "POST .../{id}/approve {note?}", "-> approved; creates the Store (pending_activation) with a unique slug"],
        ["Reject", "POST .../{id}/reject {reason}", "-> rejected (terminal); reason shown to the seller"],
    ]),

    P("6.9 Storefront: browse and product page", "h2"),
    flow([
        ["Landing page", "GET /banners?placement=hero|top_discount, GET /products/featured|best-sellers|new-arrivals|top-discounted, GET /categories", "Public. Cards carry is_wishlisted when a Bearer token is sent"],
        ["Categories page: sidebar", "GET /products/filters", "Category tree with counts, brands, colours, price range - one call"],
        ["Categories page: grid", "GET /products?category=beauty&brand=nars&color=Black&price_min=&price_max=&in_stock=true&q=&sort=new|price_asc|price_desc|rating|popular&page=1&per_page=12",
         "{items, total, page, per_page, pages}; category includes sub-categories; repeat brand/color params for multi-select"],
        ["Product page", "GET /products/{slug}", "Options (Size/Shade/Color) with values, variants (option_value_ids, price, stock), key_benefits, usage_steps, ratings breakdown, store"],
        ["Pick options -> variant", "client-side", "Find the variant whose option_value_ids match the selected values; disable combos with in_stock=false"],
        ["Reviews tab", "GET /products/{slug}/reviews?sort=new|top|low&page=, GET /products/{slug}/reviews/summary", "Approved reviews only; names abbreviated ('Ayesha K.')"],
        ["'Submit Review'", "POST /products/{slug}/reviews (multipart: name?, email?, rating, body, files[])",
         "Guests must send name + email; members default to their profile. Stored pending; 'Verified Purchase' when the member has a delivered order for it"],
        ["'You may also like'", "GET /products/{slug}/similar (or /related)", "similar = embedding neighbours, topped up by same category/brand; related = the old rule-only version"],
        ["'Frequently bought together'", "GET /products/{slug}/frequently-bought-together", "Order co-occurrence (cancelled orders ignored), cached 10 min; falls back to similar"],
        ["Wishlist heart / Wishlist tab", "PUT|DELETE /me/wishlist/{productId}, GET /me/wishlist", "PUT is idempotent"],
    ]),

    P("6.10 Cart and checkout", "h2"),
    NOTE("Guests: the first call to any /cart endpoint returns <font face='Courier'>cart_token</font>. Store it (localStorage) and send it as the "
         "<font face='Courier'>X-Cart-Token</font> header on every cart/checkout call. When the user signs in, send both the Bearer token and the header once - "
         "the guest cart merges into their account cart and the token is retired."),
    flow([
        ["Cart page opens", "GET /cart", "items (with variant_summary, image, stock), order_note, voucher, delivery_method, summary {subtotal, shipping, discount, total}, can_checkout, problems[]"],
        ["'Add to Cart' / 'Buy Now'", "POST /cart/items {variant_id, qty}", "Merges quantity if already present; clamps to stock; 409 when sold out"],
        ["Quantity stepper / remove", "PATCH /cart/items/{id} {qty}, DELETE /cart/items/{id}", "409 if qty exceeds stock"],
        ["'Order Notes', 'Delivery Type'", "PATCH /cart {order_note?, delivery_method_id?}", "Shipping cost in the summary follows the chosen method (default: Standard)"],
        ["'Discount Voucher -> Apply'", "POST /cart/voucher {code}, DELETE /cart/voucher", "404 unknown; 422 with the reason (expired, minimum not met, already used)"],
        ["Checkout page: delivery options", "GET /delivery-methods", "'Standard Delivery 5 - 7 working days $10.99', 'Express ...'"],
        ["'Place Order'", "POST /checkout {contact, address | address_id, ship_to_different_address?, shipping_address?, delivery_method_id, payment_method_type, payment_method_id?, special_note?}",
         "201 {order, detail}. Re-checks stock under a lock, snapshots everything, reserves stock, redeems the voucher, clears the cart, emails confirmation. Order is pending_payment / unpaid."],
        ["Card entry / 'Save card for future checkout'", "<i>next sprint</i>", "The PSP SDK will tokenise the card client-side; the API will never see PAN/CVV"],
        ["'Order Placed!' screen", "use the checkout response", "detail carries the 'Thank You, John Ali...' copy; order.order_number for the confirmation"],
    ]),

    P("6.11 My Orders and tracking", "h2"),
    flow([
        ["Tabs All / Ongoing / Completed / Cancelled", "GET /me/orders?tab=all|ongoing|completed|cancelled&page=", "Each card: order_number, status_label, group, items (qty, variant_summary, prices), can_cancel, can_reorder, items[].can_review"],
        ["'View Detail' / 'Track Order' (member)", "GET /me/orders/{id}", "Adds addresses, delivery method, totals, events[] timeline (status, label, note, location, time)"],
        ["'Cancel'", "POST /me/orders/{id}/cancel {reason?}", "Allowed until the order is processing; restores stock; 409 afterwards"],
        ["'Re-Order'", "POST /me/orders/{id}/reorder", "Puts still-available lines back in the cart and returns the cart; unavailable ones are listed in problems[]"],
        ["'Leave Review'", "POST /products/{slug}/reviews (6.9)", "items[].can_review is true on delivered, un-reviewed lines"],
        ["Track Order page (guest)", "POST /orders/track {order_number, email}", "Public; matches the email used at checkout; returns status + events"],
        ["Ops move an order (no screen)", "POST /admin/orders/{id}/status {status, note?, location?}", "confirmed -> processing -> shipped -> out_for_delivery -> delivered; each writes a timeline event"],
    ]),

    P("6.12 Search and recommendations", "h2"),
    P("Search is <b>hybrid</b>: every sidebar filter is applied first (so results stay exact), then the remaining "
      "candidates are ranked by two independent rankers fused with Reciprocal Rank Fusion - keyword (Postgres websearch "
      "full-text over a per-product text card + trigram similarity on the name for typos) and semantic (cosine nearest "
      "neighbours of the query embedding via pgvector). A product found by both rises; an exact brand/SKU still wins on "
      "keywords alone; nonsense queries return nothing because each ranker has a relevance floor. "
      "Recommendations reuse the same embeddings: a shopper's <b>taste profile</b> is the weighted mean of what they viewed "
      "(1), wishlisted (3), added to cart (4) or bought (6), halved every 30 days; 'Recommended for you' is its nearest "
      "neighbours minus everything they already interacted with. Behaviour is recorded server-side (discovery.ProductEvent) "
      "from the existing endpoints - the frontend sends no tracking calls; guests are keyed by X-Cart-Token."),
    flow([
        ["Header search box (typeahead)", "GET /search/suggest?q=lip", "Up to 5 products, brands and categories matching the prefix (trigram-tolerant on Postgres)"],
        ["'Search Products' / results page", "GET /products?q=...&category=&price_max=&sort=", "sort defaults to relevance when q is given; any explicit sort (price_asc...) is honoured over the matched set. Logged with result_count (0 = nothing found)"],
        ["PDP 'You may also like'", "GET /products/{slug}/similar?limit=6", "Semantic neighbours across the whole public catalogue"],
        ["PDP / cart 'Frequently bought together'", "GET /products/{slug}/frequently-bought-together?limit=4", "From real orders; similar() until enough orders exist"],
        ["Home 'Recommended for you'", "GET /recommendations?limit=8 (+ Bearer or X-Cart-Token)", "strategy=personalised with seeds[] ('Because you viewed ...') or strategy=trending until there is history"],
        ["Ops: switch to a real embedding model", "EMBEDDING_PROVIDER=voyage + VOYAGE_API_KEY, then manage.py embed_products --all", "Default 'hash' provider is offline/lexical (dev + tests); the worker service keeps embeddings fresh as products change"],
    ]),

    P("6.13 Seller dashboard", "h2"),
    P("Every route below needs a seller <b>with an active store</b> (403 <font face='Courier'>no_active_store</font> otherwise, so the "
      "portal can send the user back to the application status page). Everything shown is computed <b>per store</b>: a buyer "
      "order that spans several stores appears to each seller as only <i>their</i> lines (a <b>store order</b>) with its own "
      "fulfilment status; the buyer-facing order status is derived from the parts. KPI cards return "
      "<font face='Courier'>{value, previous, change_pct}</font> for the requested <font face='Courier'>period</font> "
      "(today | week | month | quarter | year). Full field contract: docs/sprint-3-plan.md."),
    flow([
        ["Seller Dashboard (home)", "GET /seller/dashboard?period=month", "sales (booked) vs revenue (delivered), orders, products, low stock, 12-month series, performance (views, conversion, AOV, customer growth), latest 5 orders, stock breakdown"],
        ["Header 'Search anything...'", "GET /seller/search?q=", "Own products, order numbers/customers, customers"],
        ["Products table + tabs + KPI cards", "GET /seller/products?status=&q=&brand=&category=&stock=&sort=, GET /seller/products/kpis", "Tabs all/active/pending/draft/rejected/archived; sold_qty and earned_cents per row"],
        ["'+ Add New Product' step 1 Basic Info", "POST /seller/products {name, category_id, brand_id?, tags[], short_description}", "Creates a draft with one default variant; category_id is the leaf (subcategory)"],
        ["Steps 2-4 (Media, Pricing & Inventory, Shipping)", "POST .../images (multipart), PUT .../images/order, PATCH /seller/products/{id} (price, compare-at, threshold, shipping), PUT .../options {options}, PATCH .../variants/{vid} {sku, price_cents, stock_qty}", "Options regenerate variants for every combination, keeping matching SKUs/stock; stock changes are logged movements"],
        ["Step 5 Review -> submit", "POST /seller/products/{id}/status {action: submit}", "422 lists what is missing (image, price, variant, stock); product becomes pending until staff approve"],
        ["Staff review (no screen yet)", "GET /admin/products?status=pending, POST /admin/products/{id}/approve | reject {reason}", "Approve publishes; reject stores the reason the seller sees under the Rejected tab"],
        ["Row actions", "unpublish/archive/unarchive via .../status, POST .../duplicate, DELETE (draft/rejected/archived only), GET .../analytics", "Analytics: views, sold, earned, conversion, 30-day series"],
        ["Inventory", "GET /seller/inventory?tab=all|low|out|recent, GET /seller/inventory/kpis, POST /seller/inventory/{variant_id}/adjust {set | delta, note}, GET .../movements, GET /seller/inventory/export.csv", "on_hand = sum of variant stock; reserved = paid but unshipped units; every adjustment is a StockMovement"],
        ["Orders Overview", "GET /seller/orders/overview?period=", "KPIs, monthly delivered/pending/refunded, status share, latest 5"],
        ["Orders table", "GET /seller/orders?tab=pending|processing|shipped|delivered|cancelled|returned&q=&date_from=&date_to=", "Unpaid orders are not shown to sellers (Q27)"],
        ["View Order", "GET /seller/orders/{id}", "Customer info, addresses, payment, per-store timeline (Placed, Payment Confirmed, Processing, Packed, Shipped, Delivered), this store's items, summary with multi_store flag, allowed_next"],
        ["'Update Status' / 'Cancel Order'", "POST /seller/orders/{id}/status {status, note?}, POST .../cancel {reason}", "pending -> processing -> packed -> shipped -> delivered; returned after delivery; cancel/return restore stock"],
        ["'Print / Download Invoice', 'Export'", "GET /seller/orders/{id}/invoice, GET /seller/orders/export.csv", "Invoice data only - PDF layout is a UI concern until designed (Q28)"],
        ["Customers Overview / table / detail", "GET /seller/customers/overview, GET /seller/customers?tab=new|returning|one_time&q=&status=, GET /seller/customers/{key}", "Derived from orders; key = user id or guest email; type/status rules in sprint-3-plan.md section 5"],
        ["'Add Note', 'Export Customers'", "POST /seller/customers/{key}/notes {body}, DELETE .../notes/{id}, GET /seller/customers/export.csv", "Notes are private to the store"],
    ]),

    P("6.14 Support and marketing", "h2"),
    flow([
        ["Contact Us 'Send a Message' / 'Contact Seller Support'", "POST /contact {first_name, last_name, email, phone?, subject, message}", "Subjects from GET /site-info; linked to the seller application when subject=seller_support and signed in"],
        ["Footer 'Subscribe Now'", "POST /newsletter/subscribe {email}", "201 new, 200 already subscribed"],
        ["Footer contact info / 'We Accept'", "GET /site-info", "Values maintained in Django admin (Site settings)"],
    ]),
]

# --- 7. Endpoint reference ---------------------------------------------------
story += [
    PageBreak(),
    P("7. Endpoint reference", "h1"),
    P("All paths are prefixed with <font face='Courier'>/api/v1</font>. 'Auth' = requires <font face='Courier'>Authorization: Bearer</font>; "
      "'store' = seller with an active store; 'staff' = is_staff."),
    table([
        ["Method + path", "Auth", "Request body", "Success", "Errors"],
        ["POST /auth/register", "-", "RegisterIn", "201 AuthOut", "409, 422"],
        ["POST /auth/seller/register", "-", "SellerRegisterIn", "201 AuthOut", "409, 422"],
        ["POST /auth/login", "-", "LoginIn", "200 AuthOut", "401, 403"],
        ["POST /auth/refresh", "-", "RefreshIn", "200 TokenOut", "401"],
        ["POST /auth/logout", "yes", "LogoutIn", "204", "401"],
        ["POST /auth/otp/send", "optional", "OtpSendIn", "200 OtpSendOut", "422, 429"],
        ["POST /auth/otp/verify", "-", "OtpVerifyIn", "200 MessageOut", "400, 429"],
        ["POST /auth/password/forgot", "-", "PasswordForgotIn", "200 MessageOut", "429"],
        ["POST /auth/password/reset", "-", "PasswordResetIn", "200 MessageOut", "400, 422"],
        ["GET /me", "yes", "-", "200 UserOut", "401"],
        ["PATCH /me", "yes", "UserPatchIn", "200 UserOut", "422"],
        ["POST /me/avatar", "yes", "multipart file", "200 UserOut", "422"],
        ["POST /me/password", "yes", "PasswordChangeIn", "200 MessageOut", "400, 422"],
        ["POST /me/terms", "yes", "TermsAcceptIn", "201 MessageOut", ""],
        ["GET /me/addresses", "yes", "-", "200 [AddressOut]", ""],
        ["POST /me/addresses", "yes", "AddressIn", "201 AddressOut", "422"],
        ["GET /me/addresses/{id}", "yes", "-", "200 AddressOut", "404"],
        ["PATCH /me/addresses/{id}", "yes", "AddressPatchIn", "200 AddressOut", "404, 422"],
        ["POST /me/addresses/{id}/default", "yes", "-", "200 AddressOut", "404"],
        ["DELETE /me/addresses/{id}", "yes", "-", "204", "404"],
        ["GET /me/payment-methods", "yes", "-", "200 [PaymentMethodOut]", ""],
        ["POST /me/payment-methods", "yes", "PaymentMethodIn", "201 PaymentMethodOut", "422"],
        ["POST /me/payment-methods/{id}/default", "yes", "-", "200 PaymentMethodOut", "404"],
        ["DELETE /me/payment-methods/{id}", "yes", "-", "204", "404"],
        ["GET /me/wishlist", "yes", "-", "200 [WishlistItemOut]", ""],
        ["PUT /me/wishlist/{productId}", "yes", "-", "201 WishlistItemOut", "404"],
        ["DELETE /me/wishlist/{productId}", "yes", "-", "204", ""],
        ["GET /me/orders?tab=", "yes", "-", "200 Page[OrderSummaryOut]", ""],
        ["GET /me/orders/{id}", "yes", "-", "200 OrderDetailOut", "404"],
        ["POST /me/orders/{id}/cancel", "yes", "CancelIn?", "200 OrderDetailOut", "404, 409"],
        ["POST /me/orders/{id}/reorder", "yes", "-", "200 CartOut", "404"],
        ["GET /health", "-", "-", "200 {status: ok}", ""],
    ], (52 * mm, 14 * mm, 34 * mm, 42 * mm, 30 * mm)),
    P("Seller portal (Bearer token of a seller account)", "h2"),
    table([
        ["Method + path", "Auth", "Request body", "Success", "Errors"],
        ["GET /seller/application", "seller", "-", "200 ApplicationOut", "404"],
        ["PATCH /seller/application/steps/basic-info", "seller", "BasicInfoIn", "200 ApplicationOut", "409, 422"],
        ["PATCH /seller/application/steps/business-info", "seller", "BusinessInfoIn", "200 ApplicationOut", "409, 422"],
        ["PATCH /seller/application/steps/representative", "seller", "RepresentativeIn", "200 ApplicationOut", "409, 422"],
        ["PATCH /seller/application/steps/banking", "seller", "BankingIn", "200 ApplicationOut", "409, 422"],
        ["POST /seller/application/directors", "seller", "DirectorIn", "201 DirectorOut", "409, 422"],
        ["PATCH|DELETE /seller/application/directors/{id}", "seller", "DirectorIn", "200 DirectorOut | 204", "404, 409"],
        ["GET /seller/application/documents", "seller", "-", "200 [DocumentOut]", ""],
        ["POST /seller/application/documents", "seller", "multipart type + file", "201 DocumentOut", "409, 422"],
        ["POST /seller/application/submit", "seller", "SubmitIn", "200 ApplicationStatusOut", "409, 422"],
        ["POST /seller/application/resubmit", "seller", "ResubmitIn", "200 ApplicationStatusOut", "409, 422"],
        ["GET /seller/application/status", "seller", "-", "200 ApplicationStatusOut", "404"],
        ["GET /seller/store/slug-available?slug=", "seller", "-", "200 SlugAvailableOut", ""],
        ["POST /seller/store/activate", "seller", "StoreActivateIn", "200 StoreOut", "409, 422"],
        ["GET /seller/store", "seller", "-", "200 StoreOut", "404"],
        ["GET /lookups/seller", "-", "-", "200 SellerLookupsOut", ""],
        ["GET /lookups/banks?country=", "-", "-", "200 [BankOut]", ""],
    ], (52 * mm, 14 * mm, 34 * mm, 42 * mm, 30 * mm)),
    P("Catalogue (public; send a Bearer token to get is_wishlisted)", "h2"),
    table([
        ["Method + path", "Auth", "Request body", "Success", "Errors"],
        ["GET /categories", "-", "-", "200 [CategoryOut] (tree)", ""],
        ["GET /brands", "-", "-", "200 [BrandOut]", ""],
        ["GET /products/filters", "-", "-", "200 ProductFiltersOut", ""],
        ["GET /products?...", "optional", "query: ProductQuery", "200 Page[ProductCardOut]", "404 (category), 422"],
        ["GET /products/featured | best-sellers | new-arrivals | top-discounted", "optional", "?limit=", "200 [ProductCardOut]", ""],
        ["GET /products/{slug}", "optional", "-", "200 ProductDetailOut", "404"],
        ["GET /products/{slug}/related", "optional", "?limit=", "200 [ProductCardOut]", "404"],
        ["GET /products/{slug}/similar", "optional", "?limit=", "200 [ProductCardOut]", "404"],
        ["GET /products/{slug}/frequently-bought-together", "optional", "?limit=", "200 [ProductCardOut]", "404"],
        ["GET /search/suggest", "-", "?q=&limit=", "200 SuggestOut", "422"],
        ["GET /recommendations", "optional", "?limit= (+X-Cart-Token)", "200 RecommendationsOut", ""],
        ["GET /seller/dashboard", "store", "?period=", "200 DashboardOut", "403"],
        ["GET /seller/search", "store", "?q=&limit=", "200 PortalSearchOut", "403"],
        ["GET /seller/products", "store", "SellerProductQuery + page", "200 page[SellerProductRowOut]", "403"],
        ["GET /seller/products/kpis", "store", "?period=", "200 SellerProductKpisOut", ""],
        ["POST /seller/products", "store", "ProductCreateIn", "201 SellerProductOut", "422"],
        ["GET|PATCH|DELETE /seller/products/{id}", "store", "ProductPatchIn", "200 SellerProductOut / 204", "404, 409, 422"],
        ["PUT /seller/products/{id}/options", "store", "OptionsIn", "200 SellerProductOut", "404, 409, 422"],
        ["PATCH /seller/products/{id}/variants/{vid}", "store", "VariantPatchIn", "200 SellerVariantOut", "404, 409, 422"],
        ["POST /seller/products/{id}/images", "store", "multipart file, alt", "201 SellerImageOut", "404, 422"],
        ["PUT /seller/products/{id}/images/order", "store", "ImageOrderIn", "200 SellerProductOut", "422"],
        ["DELETE /seller/products/{id}/images/{iid}", "store", "-", "204", "404"],
        ["PUT /seller/products/{id}/content", "store", "ContentIn", "200 SellerProductOut", ""],
        ["POST /seller/products/{id}/status", "store", "StatusChangeIn", "200 SellerProductOut", "409, 422"],
        ["POST /seller/products/{id}/duplicate", "store", "-", "201 SellerProductOut", "404"],
        ["GET /seller/products/{id}/analytics", "store", "?period=", "200 ProductAnalyticsOut", "404"],
        ["GET /admin/products", "staff", "?status=&page", "200 page[AdminProductRowOut]", ""],
        ["POST /admin/products/{id}/approve|reject", "staff", "RejectIn (reject)", "200 MessageOut", "404, 409"],
        ["GET /seller/inventory", "store", "InventoryQuery + page", "200 page[InventoryRowOut]", ""],
        ["GET /seller/inventory/kpis", "store", "-", "200 InventoryKpisOut", ""],
        ["POST /seller/inventory/{vid}/adjust", "store", "StockAdjustIn", "200 InventoryVariantOut", "404, 409, 422"],
        ["GET /seller/inventory/{vid}/movements", "store", "?limit=", "200 [StockMovementOut]", "404"],
        ["GET /seller/inventory/export.csv", "store", "-", "200 text/csv", ""],
        ["GET /seller/orders/overview", "store", "?period=", "200 SellerOrdersOverviewOut", ""],
        ["GET /seller/orders", "store", "SellerOrderQuery + page", "200 page[SellerOrderRowOut]", ""],
        ["GET /seller/orders/tabs", "store", "-", "200 OrderTabsOut", ""],
        ["GET /seller/orders/{id}", "store", "-", "200 SellerOrderDetailOut", "404"],
        ["POST /seller/orders/{id}/status", "store", "OrderStatusIn", "200 SellerOrderDetailOut", "404, 409"],
        ["POST /seller/orders/{id}/cancel", "store", "OrderCancelIn", "200 SellerOrderDetailOut", "404, 409"],
        ["GET /seller/orders/{id}/invoice", "store", "-", "200 SellerInvoiceOut", "404"],
        ["GET /seller/orders/export.csv", "store", "-", "200 text/csv", ""],
        ["GET /seller/customers/overview", "store", "?period=", "200 CustomersOverviewOut", ""],
        ["GET /seller/customers", "store", "CustomerQuery + page", "200 page[CustomerRowOut]", ""],
        ["GET /seller/customers/tabs", "store", "-", "200 CustomerTabsOut", ""],
        ["GET /seller/customers/{key}", "store", "-", "200 CustomerDetailOut", "404"],
        ["POST /seller/customers/{key}/notes", "store", "CustomerNoteIn", "201 CustomerNoteOut", "404"],
        ["DELETE /seller/customers/{key}/notes/{id}", "store", "-", "204", "404"],
        ["GET /seller/customers/export.csv", "store", "-", "200 text/csv", ""],
        ["GET /products/{slug}/reviews?sort=", "-", "-", "200 Page[ReviewOut]", "404"],
        ["GET /products/{slug}/reviews/summary", "-", "-", "200 RatingBreakdownOut", "404"],
        ["POST /products/{slug}/reviews", "optional", "multipart ReviewIn + files[]", "201 ReviewCreatedOut", "404, 409, 422"],
    ], (52 * mm, 14 * mm, 34 * mm, 42 * mm, 30 * mm)),
    P("Cart, checkout, tracking, support (guests: X-Cart-Token header)", "h2"),
    table([
        ["Method + path", "Auth", "Request body", "Success", "Errors"],
        ["GET /cart", "optional", "-", "200 CartOut", ""],
        ["POST /cart/items", "optional", "CartItemIn", "200 CartOut", "404, 409"],
        ["PATCH|DELETE /cart/items/{id}", "optional", "CartItemPatchIn", "200 CartOut", "404, 409"],
        ["PATCH /cart", "optional", "CartPatchIn", "200 CartOut", "422"],
        ["POST|DELETE /cart/voucher", "optional", "VoucherApplyIn", "200 CartOut", "404, 422"],
        ["GET /delivery-methods", "-", "-", "200 [DeliveryMethodOut]", ""],
        ["POST /checkout", "optional", "CheckoutIn", "201 OrderPlacedOut", "404, 409, 422"],
        ["POST /orders/track", "-", "TrackIn", "200 TrackOut", "404"],
        ["GET /banners?placement=", "-", "-", "200 [BannerOut]", "422"],
        ["POST /newsletter/subscribe", "-", "NewsletterSubscribeIn", "201 | 200 MessageOut", "422"],
        ["POST /contact", "optional", "ContactIn", "201 ContactCreatedOut", "422"],
        ["GET /site-info", "-", "-", "200 SiteInfoOut", ""],
    ], (52 * mm, 14 * mm, 34 * mm, 42 * mm, 30 * mm)),
    P("Admin (Bearer token of a staff account)", "h2"),
    table([
        ["Method + path", "Auth", "Request body", "Success", "Errors"],
        ["GET /admin/seller-applications?status=&account_type=&q=", "staff", "-", "200 Page[AdminApplicationListOut]", ""],
        ["GET /admin/seller-applications/{id}", "staff", "-", "200 AdminApplicationOut", "404"],
        ["POST .../{id}/start-review | approve | reject | request-info", "staff", "ApproveIn | RejectIn | RequestInfoIn", "200 AdminApplicationOut", "404, 409, 422"],
        ["POST .../{id}/documents/{docId}/review", "staff", "DocumentReviewIn", "200 DocumentOut", "404"],
        ["GET /admin/orders?status=&q=", "staff", "-", "200 Page[OrderSummaryOut]", ""],
        ["GET /admin/orders/{id}", "staff", "-", "200 OrderDetailOut", "404"],
        ["POST /admin/orders/{id}/status", "staff", "AdminOrderStatusIn", "200 OrderDetailOut", "404, 409"],
    ], (52 * mm, 14 * mm, 34 * mm, 42 * mm, 30 * mm)),
    P("Key schemas", "h2"),
    P("Abridged - Swagger has every field. <font face='Courier'>Page[T]</font> means {items: T[], total, page, per_page, pages}."),
    CODE("""
UserOut {
  id: uuid, email, phone, full_name, first_name, last_name,
  gender: "male"|"female"|"other"|"prefer_not_to_say"|"",
  date_of_birth: "YYYY-MM-DD"|null, profession, country: "PK", city, address_line,
  status: "active"|"suspended"|"deleted", is_seller, is_staff,
  email_verified: bool, phone_verified: bool, member_since: ISO datetime, avatar_url|null
}
TokenOut  { access_token, refresh_token, token_type: "bearer", expires_in: 900 }
AuthOut   { user: UserOut, tokens: TokenOut }
AddressOut { id, type: "home"|"office"|"other", label, full_name, phone, street_address, unit_floor,
             city, province, postal_code, country, delivery_instructions, is_default, created_at, updated_at }
PaymentMethodOut { id, type: "card"|"paypal"|"apple_pay"|"google_pay"|"bank_transfer",
                   card_brand: "visa"|"mastercard"|"other"|"", card_holder_name, last4,
                   exp_month, exp_year, provider, is_default, display_name, created_at }
ProductCardOut { id, slug, name, category {id, name, slug}, brand, price_cents, compare_at_price_cents,
                 discount_percent, currency, image_url, stock_status, rating_avg, rating_count, is_wishlisted? }
ProductDetailOut = ProductCardOut + { short_description, description_title, description, images[],
                 option_types[{id, name, values[{id, value, swatch_hex}]}],
                 variants[{id, sku, option_value_ids[], price_cents, compare_at_price_cents, stock_qty, in_stock}],
                 key_benefits[{title, body}], usage_steps[{title, body}], store {id, display_name, slug},
                 ratings {average, count, distribution {5..1}} }
CartOut { id, cart_token?, items[{id, variant_id, product_slug, product_name, variant_summary, image_url,
          unit_price_cents, compare_at_price_cents, qty, line_total_cents, available_qty, in_stock}],
          order_note, voucher {code, type, value, label}?, delivery_method {id, name, eta_label, price_cents}?,
          summary {subtotal_cents, shipping_cents, discount_cents, total_cents, currency, item_count},
          can_checkout, problems[] }
OrderSummaryOut { id, order_number, status, status_label, group: ongoing|completed|cancelled, placed_at,
          total_cents, currency, payment_method_type, payment_status, items[...OrderItemOut], can_cancel, can_reorder }
ApplicationOut { id, application_number, account_type, status, status_headline, editable, current_step,
          steps[{step, name, required, complete, missing_fields[], missing_documents[]}], can_submit,
          basic_info, business_info, representative, banking (masked), directors[], documents[],
          open_info_request {message, items[{kind, target, instruction, resolved}]}?, store? }
"""),
]

# --- 8. Conventions ----------------------------------------------------------
story += [
    P("8. Data conventions for the frontend", "h1"),
    table([
        ["Topic", "Convention"],
        ["IDs", "UUID strings everywhere (users, addresses, products, orders...)"],
        ["Dates/times", "ISO 8601 in UTC, e.g. 2026-09-12T20:39:00Z. Dates without time are YYYY-MM-DD. Format for display client-side."],
        ["Money", "Integers in minor units: price_cents = 2599 means $25.99, alongside a currency code. Never send floats."],
        ["Phone numbers", "E.164 (+923001234567). The API accepts spaces/dashes/parentheses and normalises; display formatting is the client's job."],
        ["Email", "Lower-cased by the server"],
        ["Enums", "Lower-snake_case strings exactly as listed in Swagger. Unknown values are rejected with 422."],
        ["Optional text fields", "Come back as \"\" (empty string) when unset, not null; nullable dates/numbers come back as null."],
        ["Partial updates", "Use PATCH and send only the keys that changed. Sending null clears a field."],
        ["Lists", "Small lists (addresses, cards, wishlist) are plain arrays. Products, reviews, orders and admin queues are paginated: {items, total, page, per_page, pages}; per_page defaults to 12 (max 100)."],
        ["Guest cart", "Keep the cart_token from the first /cart response and send it as X-Cart-Token; send it once together with the Bearer token after login to merge."],
        ["Masked PII", "Sellers see their own ID / account numbers masked (id_passport_number_masked, account_number_last4); only staff endpoints return the full values."],
        ["File uploads", "multipart/form-data with the field named 'file' (avatar) or per the endpoint; size/type limits are enforced server-side and returned as 422."],
        ["Idempotency", "Setting a default twice, or verifying an already-verified email, is safe."],
    ], (34 * mm, 138 * mm)),
]

# --- 9. Working with the API -------------------------------------------------
story += [
    P("9. Working with the API day to day", "h1"),
    P("Trying calls in Swagger", "h2"),
    B("Open /api/v1/docs, expand <b>POST /auth/register</b>, 'Try it out', submit. Copy <font face='Courier'>tokens.access_token</font> from the response."),
    B("Click <b>Authorize</b> (top right), paste the access token (without 'Bearer '), then any /me endpoint works."),
    B("For OTP codes, look at the terminal running <font face='Courier'>runserver</font>."),
    P("Generating a typed client", "h2"),
    CODE("""
# TypeScript types straight from the live spec
npx openapi-typescript http://127.0.0.1:8000/api/v1/openapi.json -o src/api/schema.d.ts
"""),
    P("Regenerate the client whenever the backend announces a schema change; the spec is the contract."),
    P("Where to look when something is unclear", "h2"),
    table([
        ["Question", "Source"],
        ["What fields does endpoint X accept and return?", "Swagger /api/v1/docs (authoritative), then backend/asknget/&lt;app&gt;/schemas.py"],
        ["Why did the API reject my payload?", "The 422 body's errors[]; rules are in schemas.py, business rules in services.py"],
        ["Which tables/fields exist and why?", "docs/data-model.md (every table annotated with the Figma label it came from)"],
        ["Has the design changed since the backend was built?", "python docs/figma/snapshot.py --check, then git diff docs/figma/"],
        ["Open product questions (currency, guest checkout, multi-vendor shipping...)", "docs/data-model.md section 10"],
    ], (74 * mm, 98 * mm)),
    P("Roadmap (subject to sprint planning)", "h2"),
    table([
        ["Order", "Scope", "Depends on"],
        ["1", "Payment provider: client-side tokenisation, POST /checkout/{id}/pay, webhooks -> payment_status/paid, saved cards, refunds on cancel", "Provider decision (see data-model.md section 10)"],
        ["2", "Seller portal remainder: Returns flow, Chat, Analytics page, Notifications, Team/Managers (sub-roles), invoice PDF, social login", "Design for those screens + answers to Q20-Q34"],
        ["3", "Review moderation UI, category/brand management endpoints (Django admin covers these today)", "-"],
        ["4", "Vendor sub-roles/permissions, premium tier, vendor-customer messaging", "Product decisions + design"],
        ["5", "Email/SMS providers; more worker jobs (category counts, rating refresh, cart expiry)", "Worker service exists; add commands"],
        ["6", "LLM query understanding ('gift under $50 for dry skin' -> filters) and a streaming shopping assistant", "Claude API key; ASGI (uvicorn) for streaming - only those endpoints go async"],
    ], (14 * mm, 112 * mm, 46 * mm)),
    Spacer(1, 6 * mm),
    P("Repository: https://github.com/AunSyedShah/asknget &nbsp;&nbsp;|&nbsp;&nbsp; Backend README: backend/README.md &nbsp;&nbsp;|&nbsp;&nbsp; "
      "This document is generated by docs/build_developer_guide.py - edit the script, not the PDF.", "small"),
]

# --------------------------------------------------------------------------- #
doc = Doc(
    str(OUT), pagesize=A4, leftMargin=18 * mm, rightMargin=18 * mm, topMargin=16 * mm, bottomMargin=20 * mm,
    title="asknget Backend - Developer Guide", author="asknget backend team", subject="Architecture, API flows, setup",
)
doc.multiBuild(story, onFirstPage=on_page, onLaterPages=on_page)
print(f"wrote {OUT} ({OUT.stat().st_size // 1024} KB)")

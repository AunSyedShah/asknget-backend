# asknget backend

Django 6 + django-ninja API for the asknget marketplace (buyer storefront + seller onboarding).
The data model is derived from the Figma design — see [`../docs/data-model.md`](../docs/data-model.md).

## Layout

```
backend/
├── asknget/              # Django project root (manage.py lives here)
│   ├── asknget/          # settings, urls, api (NinjaAPI root)
│   ├── core/             # abstract bases, EncryptedTextField, Bank + SiteSetting lookups, seed command
│   ├── accounts/         # User (email login), OTP, refresh tokens, terms audit, addresses, payment methods
│   ├── catalog/          # categories, brands, products, variants/options, reviews, wishlist
│   ├── sellers/          # seller KYC application, documents, info requests, stores
│   ├── orders/           # cart, vouchers, delivery methods, orders, status timeline, payments
│   └── marketing/        # banners, newsletter, contact messages
├── .env.example
└── pyproject.toml        # managed with uv
```

## Setup

```bash
uv sync
cp .env.example .env            # then set FIELD_ENCRYPTION_KEY (see comment in the file)
cd asknget
uv run python manage.py migrate
uv run python manage.py seed_lookups        # categories, brands, banks, delivery methods
uv run python manage.py createsuperuser
uv run python manage.py runserver
```

- API docs (Swagger): http://127.0.0.1:8000/api/v1/docs · OpenAPI JSON: /api/v1/openapi.json
- Admin: http://127.0.0.1:8000/admin/

Without `DATABASE_URL` the project falls back to SQLite; set it to a Postgres URL for anything beyond local experiments.

## Conventions

- UUID primary keys, `created_at` / `updated_at` everywhere (`core.models.TimeStampedModel`); soft delete via `SoftDeleteModel` where the design implies history (users' addresses, cards, products, reviews).
- Money as integer minor units (`*_cents`) + `currency`.
- PII that must never be queried in bulk (ID/passport numbers, bank account numbers) uses `core.fields.EncryptedTextField` (Fernet, keyed by `FIELD_ENCRYPTION_KEY`); a plain `last4` column is kept where the UI needs to display something.
- Card PAN / CVV are never stored — only a PSP token + brand + last4.

## API layer conventions (django-ninja)

Every app is split into three files so sprint-to-sprint changes land in one place:

| file | owns | changes when… |
|---|---|---|
| `<app>/schemas.py` | request/response shapes + field validation | the UI form changes |
| `<app>/services.py` | business rules (uniqueness, state machines, side effects) | a flow/rule changes |
| `<app>/api.py` | `Router`s — thin: parse → call service → return | an endpoint is added/renamed |

Rules:
- **Schemas**: `XIn` (create, all required fields required), `XPatchIn` (every field `Optional`, view applies `payload.dict(exclude_unset=True)` so *omitted ≠ null*), `XOut` (response, explicit `fields` list — never `"__all__"`). Reusable field types (`PhoneStr`, `EmailStr`, `PasswordStr`, `OtpCode`, `CountryCode`, …) live in `core/validators.py` — change a rule once, every schema follows.
- **Enums** in schemas reference the model `TextChoices` directly (`Address.AddressType`). Inner `TextChoices` classes must have **globally unique names** (`OrderStatus`, not `Status`) — Ninja flattens them into OpenAPI components by bare class name and silently overwrites collisions.
- **Errors**: business errors raise `ninja.errors.HttpError(status, detail)`; validation errors are flattened by `asknget/api.py` to `{"detail": "...", "errors": [{"field", "message"}]}` (422). Declare every non-200 status in `response={...}` so it shows in Swagger.
- **Auth**: `core.auth.JWTAuth` (Bearer access token, 15 min) sets `request.auth` to the `User`. Refresh tokens are opaque, hashed in DB, rotated on every use. `OptionalJWTAuth` for guest-or-user endpoints.
- **`None` → `""`**: services pass optional payload fields through `core.models.coerce_nulls(Model, data)` before hitting non-nullable `CharField`s.
- **Tests** live in `<app>/tests.py` and hit the real URLconf (`/api/v1/...`) so schema, service and router are exercised together. Run: `uv run python manage.py test`.
- Versioning: everything is mounted under `/api/v1/`; a breaking contract change means a `v2` router, not an edit.

Mounted routers: `auth` (`/api/v1/auth`), `me` (`/api/v1/me`). Next: `catalog`, `cart`/`orders`, `sellers`, `marketing`.


"""Startup checks for settings that only break much later.

A missing or malformed FIELD_ENCRYPTION_KEY does not stop the server: everything
works until a seller submits the KYC wizard's basic-info step, which is the first
write of an encrypted column, and that request 500s with ImproperlyConfigured.
Checking at startup turns "why did this one endpoint break" into a line printed
by `runserver` / `manage.py check`.

Warnings, not errors: the Docker image builds static files with no key at all
(`collectstatic` in the Dockerfile), and `manage.py check` must stay usable there.
"""

from django.conf import settings
from django.core.checks import Warning, register

# The shared development key published in backend/.env.example (a test keeps them in sync).
DEV_ENCRYPTION_KEY = "QbUpnoY5McN7IJI0yvg4j1J7RDDQE2_meQywUQzSU_4="


@register()
def check_field_encryption_key(app_configs, **kwargs):
    """FIELD_ENCRYPTION_KEY must be present, valid, and not the public dev key outside DEBUG."""
    if getattr(settings, "TESTING", False):
        return []   # tests generate a throwaway key

    key = getattr(settings, "FIELD_ENCRYPTION_KEY", "") or ""
    if not key:
        return [Warning(
            "FIELD_ENCRYPTION_KEY is not set: saving seller ID/passport or bank account "
            "numbers will fail with ImproperlyConfigured.",
            hint="Copy backend/.env.example to backend/.env (it carries the shared development key). "
                 "For anything else generate one: python -c "
                 "\"from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())\"",
            id="core.W001",
        )]

    from cryptography.fernet import Fernet

    for name, value in [("FIELD_ENCRYPTION_KEY", key),
                        *(("FIELD_ENCRYPTION_KEY_PREVIOUS", k) for k in getattr(settings, "FIELD_ENCRYPTION_KEY_PREVIOUS", []))]:
        try:
            Fernet(value.encode() if isinstance(value, str) else value)
        except Exception:
            return [Warning(
                f"{name} holds a value that is not a valid Fernet key, so encrypted fields will fail.",
                hint="It must be 32 url-safe base64-encoded bytes; regenerate it with Fernet.generate_key(). "
                     "Replacing the key makes existing encrypted rows unreadable.",
                id="core.W002",
            )]

    if key == DEV_ENCRYPTION_KEY and not settings.DEBUG:
        return [Warning(
            "FIELD_ENCRYPTION_KEY is the shared development key from .env.example. Anyone with the repo "
            "can decrypt data stored under it.",
            hint="Generate a key for this environment, move this one to FIELD_ENCRYPTION_KEY_PREVIOUS if any "
                 "rows were written under it, then run `manage.py check_encryption --rewrite`.",
            id="core.W003",
        )]
    return []

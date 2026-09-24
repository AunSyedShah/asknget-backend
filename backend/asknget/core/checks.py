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


@register()
def check_field_encryption_key(app_configs, **kwargs):
    """FIELD_ENCRYPTION_KEY must be present and a valid Fernet key."""
    if getattr(settings, "TESTING", False):
        return []   # tests generate a throwaway key

    key = getattr(settings, "FIELD_ENCRYPTION_KEY", "") or ""
    if not key:
        return [Warning(
            "FIELD_ENCRYPTION_KEY is not set: saving seller ID/passport or bank account "
            "numbers will fail with ImproperlyConfigured.",
            hint="Add it to backend/.env (see .env.example). Generate one with: python -c "
                 "\"from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())\"",
            id="core.W001",
        )]

    from cryptography.fernet import Fernet

    try:
        Fernet(key.encode() if isinstance(key, str) else key)
    except Exception:
        return [Warning(
            "FIELD_ENCRYPTION_KEY is not a valid Fernet key, so encrypted fields will fail on write.",
            hint="It must be 32 url-safe base64-encoded bytes; regenerate it with Fernet.generate_key(). "
                 "Replacing the key makes existing encrypted rows unreadable.",
            id="core.W002",
        )]
    return []

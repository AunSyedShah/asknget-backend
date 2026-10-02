"""Field-level encryption for PII that must be stored but never read in bulk
(ID/passport numbers, bank account numbers).

Uses Fernet (AES-128-CBC + HMAC) keyed from settings.FIELD_ENCRYPTION_KEY.
Values are opaque in the DB, so they cannot be filtered or ordered on — keep a
separate plain column (e.g. `last4`) for anything the UI needs to show.
"""

from cryptography.fernet import Fernet, InvalidToken, MultiFernet
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.db import models


def configured_keys() -> list[str]:
    """Current key first, then any `FIELD_ENCRYPTION_KEY_PREVIOUS` keys.

    New values are always written with the first; old rows stay readable while
    their key is listed, and `manage.py check_encryption --rewrite` moves them to
    the first so the old one can be dropped."""
    key = getattr(settings, "FIELD_ENCRYPTION_KEY", None)
    if not key:
        raise ImproperlyConfigured(
            "FIELD_ENCRYPTION_KEY is not set. Copy backend/.env.example to backend/.env (it carries the shared "
            "development key), or generate one with "
            "`python -c \"from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())\"`"
        )
    return [key, *getattr(settings, "FIELD_ENCRYPTION_KEY_PREVIOUS", [])]


def fernet_for(keys: list[str]) -> MultiFernet:
    return MultiFernet([Fernet(k.encode() if isinstance(k, str) else k) for k in keys])


def _fernet() -> MultiFernet:
    return fernet_for(configured_keys())


class EncryptedTextField(models.TextField):
    """Transparent encrypt-on-save / decrypt-on-load text field."""

    description = "Fernet-encrypted text"

    def get_prep_value(self, value):
        if value is None or value == "":
            return value
        return _fernet().encrypt(str(value).encode()).decode()

    def from_db_value(self, value, expression, connection):
        if value is None or value == "":
            return value
        try:
            return _fernet().decrypt(value.encode()).decode()
        except InvalidToken:
            # Wrong key or legacy plaintext row — surface rather than silently corrupt.
            raise ValueError(
                f"Cannot decrypt {self.model._meta.label}.{self.name}: the row was encrypted with a different "
                "FIELD_ENCRYPTION_KEY. Run `manage.py check_encryption` to see which rows, and list the old key in "
                "FIELD_ENCRYPTION_KEY_PREVIOUS if you still have it."
            )

    def to_python(self, value):
        return value

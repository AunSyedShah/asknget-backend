"""Field-level encryption for PII that must be stored but never read in bulk
(ID/passport numbers, bank account numbers).

Uses Fernet (AES-128-CBC + HMAC) keyed from settings.FIELD_ENCRYPTION_KEY.
Values are opaque in the DB, so they cannot be filtered or ordered on — keep a
separate plain column (e.g. `last4`) for anything the UI needs to show.
"""

from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.db import models


def _fernet() -> Fernet:
    key = getattr(settings, "FIELD_ENCRYPTION_KEY", None)
    if not key:
        raise ImproperlyConfigured(
            "FIELD_ENCRYPTION_KEY is not set. Generate one with "
            "`python -c \"from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())\"`"
        )
    return Fernet(key.encode() if isinstance(key, str) else key)


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
            raise ValueError("EncryptedTextField: unable to decrypt value (key mismatch?)")

    def to_python(self, value):
        return value

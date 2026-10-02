"""Report which encrypted rows the configured key(s) can read, and optionally re-key them.

Two developers with different FIELD_ENCRYPTION_KEYs sharing a database (or a dump)
get `Cannot decrypt ...` the first time one of them lists a row the other wrote.
This answers "which rows, and is the old key still known?" without loading models,
so it works even when the ORM itself would raise.

    manage.py check_encryption             # report; exits 1 if any row is unreadable
    manage.py check_encryption --rewrite   # re-encrypt rows only an old key could read

Rows no listed key can read are never touched: they are reported so you can find the
missing key (a teammate's .env) or decide to clear them.
"""

import hashlib

from cryptography.fernet import Fernet, InvalidToken
from django.apps import apps
from django.core.management.base import BaseCommand, CommandError
from django.db import connections, transaction

from core.fields import EncryptedTextField, configured_keys


def fingerprint(key: str) -> str:
    """Enough to tell two keys apart in a chat message; useless for recovering one."""
    return hashlib.sha256(key.encode()).hexdigest()[:8]


class Command(BaseCommand):
    help = "Report which encrypted rows the configured key(s) can read; --rewrite re-keys old ones."

    def add_arguments(self, parser):
        parser.add_argument("--rewrite", action="store_true",
                            help="re-encrypt rows that only a FIELD_ENCRYPTION_KEY_PREVIOUS key can read, under the current key")
        parser.add_argument("--database", default="default")

    def handle(self, *args, rewrite=False, database="default", **options):
        keys = configured_keys()
        ferns = [Fernet(k.encode() if isinstance(k, str) else k) for k in keys]
        self.stdout.write(f"current key {fingerprint(keys[0])}"
                          + (f"; previous {', '.join(fingerprint(k) for k in keys[1:])}" if keys[1:] else "; no previous keys"))

        conn = connections[database]
        q = conn.ops.quote_name
        unreadable = rewritten = scanned = 0
        for model in apps.get_models():
            for field in model._meta.concrete_fields:
                if not isinstance(field, EncryptedTextField):
                    continue
                table, col, pk = q(model._meta.db_table), q(field.column), q(model._meta.pk.column)
                with conn.cursor() as cur:
                    cur.execute(f"SELECT {pk}, {col} FROM {table} WHERE {col} IS NOT NULL AND {col} <> ''")
                    rows = cur.fetchall()
                counts = [0] * len(ferns)
                bad = 0
                stale = []   # (pk, plaintext) readable only by an older key
                for row_pk, token in rows:
                    for i, f in enumerate(ferns):
                        try:
                            plain = f.decrypt(token.encode() if isinstance(token, str) else token)
                        except InvalidToken:
                            continue
                        counts[i] += 1
                        if i > 0:
                            stale.append((row_pk, plain))
                        break
                    else:
                        bad += 1
                scanned += len(rows)
                unreadable += bad
                if rewrite and stale:
                    with transaction.atomic(using=database), conn.cursor() as cur:
                        for row_pk, plain in stale:
                            cur.execute(f"UPDATE {table} SET {col} = %s WHERE {pk} = %s", [ferns[0].encrypt(plain).decode(), row_pk])
                    rewritten += len(stale)
                if rows:
                    parts = [f"{counts[0]} current"] + [f"{n} under previous {fingerprint(keys[i])}" for i, n in enumerate(counts) if i and n]
                    line = f"{model._meta.label}.{field.name}: " + ", ".join(parts) + (f", {bad} UNREADABLE" if bad else "")
                    self.stdout.write(self.style.ERROR(line) if bad else line)

        self.stdout.write(f"{scanned} encrypted values scanned"
                          + (f", {rewritten} re-keyed to {fingerprint(keys[0])}" if rewrite else "")
                          + (f", {unreadable} unreadable" if unreadable else ""))
        if unreadable:
            raise CommandError(
                f"{unreadable} value(s) were encrypted with a key that is not configured. Put that key in "
                "FIELD_ENCRYPTION_KEY_PREVIOUS (ask whoever wrote the rows for their FIELD_ENCRYPTION_KEY), or "
                "clear the affected fields if the data is disposable."
            )

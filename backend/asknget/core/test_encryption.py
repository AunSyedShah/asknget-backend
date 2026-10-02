"""Two developers, two FIELD_ENCRYPTION_KEYs, one database.

What went wrong in practice: rows written under one key were read under another and
the request died with a decryption error. These tests pin the ways out: list the old
key and the rows stay readable; `check_encryption` reports exactly what is unreadable;
`--rewrite` moves old rows to the current key so the old one can be dropped.
"""

from io import StringIO
from pathlib import Path

from cryptography.fernet import Fernet
from django.conf import settings
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase, override_settings

from core.checks import DEV_ENCRYPTION_KEY, check_field_encryption_key
from core.testing import make_user
from sellers.models import SellerApplication

KEY_A = Fernet.generate_key().decode()   # "the front-end engineer's"
KEY_B = Fernet.generate_key().decode()   # "mine"


def _application(id_number: str, key: str) -> str:
    """Create an application whose ID number was encrypted under `key`; returns its pk."""
    with override_settings(FIELD_ENCRYPTION_KEY=key, FIELD_ENCRYPTION_KEY_PREVIOUS=[]):
        app = SellerApplication.objects.create(
            user=make_user(is_seller=True), account_type="individual", country_id=settings.DEFAULT_COUNTRY,
            id_passport_number=id_number,
        )
    return app.pk


def _read(pk, **overrides):
    with override_settings(**overrides):
        return SellerApplication.objects.get(pk=pk).id_passport_number


class KeyMismatchTests(TestCase):
    def test_a_row_under_another_key_fails_with_a_message_that_says_what_to_do(self):
        pk = _application("4801015800085", KEY_A)
        with self.assertRaises(ValueError) as ctx:
            _read(pk, FIELD_ENCRYPTION_KEY=KEY_B, FIELD_ENCRYPTION_KEY_PREVIOUS=[])
        message = str(ctx.exception)
        self.assertIn("sellers.SellerApplication.id_passport_number", message)
        self.assertIn("check_encryption", message)
        self.assertIn("FIELD_ENCRYPTION_KEY_PREVIOUS", message)

    def test_listing_the_old_key_makes_the_row_readable_again(self):
        pk = _application("4801015800085", KEY_A)
        self.assertEqual(_read(pk, FIELD_ENCRYPTION_KEY=KEY_B, FIELD_ENCRYPTION_KEY_PREVIOUS=[KEY_A]), "4801015800085")

    def test_new_writes_use_the_current_key_not_the_old_one(self):
        pk = _application("old", KEY_A)
        with override_settings(FIELD_ENCRYPTION_KEY=KEY_B, FIELD_ENCRYPTION_KEY_PREVIOUS=[KEY_A]):
            app = SellerApplication.objects.get(pk=pk)
            app.id_passport_number = "new"
            app.save()
        # readable with B alone: it was written under B, even though A was listed
        self.assertEqual(_read(pk, FIELD_ENCRYPTION_KEY=KEY_B, FIELD_ENCRYPTION_KEY_PREVIOUS=[]), "new")


class CheckEncryptionCommandTests(TestCase):
    def run_command(self, *args, **overrides):
        out = StringIO()
        with override_settings(**overrides):
            call_command("check_encryption", *args, stdout=out)
        return out.getvalue()

    def test_reports_rows_by_which_key_reads_them(self):
        _application("111", KEY_A)
        _application("222", KEY_B)
        out = self.run_command(FIELD_ENCRYPTION_KEY=KEY_B, FIELD_ENCRYPTION_KEY_PREVIOUS=[KEY_A])
        self.assertIn("sellers.SellerApplication.id_passport_number: 1 current, 1 under previous", out)
        self.assertIn("2 encrypted values scanned", out)

    def test_unreadable_rows_fail_loudly_and_are_left_alone(self):
        pk = _application("111", KEY_A)
        with self.assertRaises(CommandError) as ctx:
            self.run_command("--rewrite", FIELD_ENCRYPTION_KEY=KEY_B, FIELD_ENCRYPTION_KEY_PREVIOUS=[])
        self.assertIn("1 value(s)", str(ctx.exception))
        # untouched: the right key still reads it
        self.assertEqual(_read(pk, FIELD_ENCRYPTION_KEY=KEY_A, FIELD_ENCRYPTION_KEY_PREVIOUS=[]), "111")

    def test_rewrite_moves_old_rows_to_the_current_key(self):
        pk = _application("111", KEY_A)
        out = self.run_command("--rewrite", FIELD_ENCRYPTION_KEY=KEY_B, FIELD_ENCRYPTION_KEY_PREVIOUS=[KEY_A])
        self.assertIn("1 re-keyed", out)
        # the old key can now be dropped entirely
        self.assertEqual(_read(pk, FIELD_ENCRYPTION_KEY=KEY_B, FIELD_ENCRYPTION_KEY_PREVIOUS=[]), "111")

    def test_a_clean_database_passes(self):
        _application("111", KEY_B)
        out = self.run_command(FIELD_ENCRYPTION_KEY=KEY_B, FIELD_ENCRYPTION_KEY_PREVIOUS=[])
        self.assertIn("1 current", out)
        self.assertNotIn("UNREADABLE", out)


class SharedDevKeyTests(TestCase):
    def ids(self, **overrides):
        with override_settings(TESTING=False, **overrides):
            return [w.id for w in check_field_encryption_key(None)]

    def test_env_example_ships_the_shared_dev_key(self):
        example = Path(settings.BASE_DIR).parent / ".env.example"
        if not example.exists():   # the container mounts only backend/asknget, not the repo checkout
            self.skipTest(".env.example is not available outside a repository checkout")
        text = example.read_text(encoding="utf-8")
        self.assertIn(f"\nFIELD_ENCRYPTION_KEY={DEV_ENCRYPTION_KEY}\n", text)
        Fernet(DEV_ENCRYPTION_KEY.encode())   # and it is a valid key

    def test_the_dev_key_is_fine_while_debugging(self):
        self.assertEqual(self.ids(FIELD_ENCRYPTION_KEY=DEV_ENCRYPTION_KEY, DEBUG=True), [])

    def test_the_dev_key_is_flagged_once_debug_is_off(self):
        self.assertEqual(self.ids(FIELD_ENCRYPTION_KEY=DEV_ENCRYPTION_KEY, DEBUG=False), ["core.W003"])

    def test_a_bad_previous_key_is_flagged(self):
        self.assertEqual(self.ids(FIELD_ENCRYPTION_KEY=KEY_A, FIELD_ENCRYPTION_KEY_PREVIOUS=["nope"], DEBUG=False), ["core.W002"])

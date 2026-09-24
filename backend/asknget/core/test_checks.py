"""The encryption key is checked at startup, not at the first encrypted write.

Without this, a missing key only shows up when a seller submits the KYC wizard's
basic-info step (the first write of an encrypted column) as a 500.
"""

from cryptography.fernet import Fernet
from django.test import SimpleTestCase, override_settings

from core.checks import check_field_encryption_key


class FieldEncryptionKeyCheckTests(SimpleTestCase):
    def ids(self):
        return [w.id for w in check_field_encryption_key(None)]

    @override_settings(TESTING=False, FIELD_ENCRYPTION_KEY=Fernet.generate_key().decode())
    def test_a_valid_key_passes(self):
        self.assertEqual(self.ids(), [])

    @override_settings(TESTING=False, FIELD_ENCRYPTION_KEY="")
    def test_a_missing_key_warns(self):
        self.assertEqual(self.ids(), ["core.W001"])

    @override_settings(TESTING=False, FIELD_ENCRYPTION_KEY="too-short-to-be-fernet")
    def test_a_malformed_key_warns_before_it_can_corrupt_anything(self):
        self.assertEqual(self.ids(), ["core.W002"])

    @override_settings(FIELD_ENCRYPTION_KEY="")
    def test_test_runs_are_exempt_because_settings_generates_a_throwaway_key(self):
        self.assertEqual(self.ids(), [])

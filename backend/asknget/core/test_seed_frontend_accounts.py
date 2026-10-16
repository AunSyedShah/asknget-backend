"""seed_frontend_accounts: working logins whose passwords reach only the env file."""

import tempfile
from io import StringIO
from pathlib import Path

from django.core.management import call_command
from django.core.management.base import CommandError

from accounts.models import User
from core.testing import ApiTestCase, make_category, make_product, make_store, make_user
from team.models import StoreMember, TeamRole


def env_values(path: Path) -> dict:
    return dict(line.split("=", 1) for line in path.read_text(encoding="utf-8").splitlines() if "=" in line and not line.startswith("#"))


class SeedFrontendAccountsTests(ApiTestCase):
    @classmethod
    def setUpTestData(cls):
        cls.owner = make_user(is_seller=True, full_name="Sirosh Bashir")
        cls.store = make_store(owner=cls.owner, name="Huda Beauty")
        cls.store.slug = "huda-beauty"
        cls.store.save(update_fields=["slug"])
        make_product(cls.store, make_category("Beauty"), "BB Cream", 2599, None, stock=5)

    def run_command(self, env_file, **kw):
        out = StringIO()
        call_command("seed_frontend_accounts", "--yes-this-is-staging", f"--env-file={env_file}", stdout=out, **kw)
        return out.getvalue()

    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.env = Path(self.dir.name) / ".env.local"

    def test_it_refuses_without_the_staging_flag(self):
        with self.assertRaises(CommandError):
            call_command("seed_frontend_accounts", f"--env-file={self.env}", stdout=StringIO())
        self.assertFalse(User.objects.filter(email__endswith="@asknget.test").exists())

    def test_passwords_reach_only_the_env_file_and_they_work(self):
        output = self.run_command(self.env)
        values = env_values(self.env)
        for prefix in ("CUSTOMER", "SELLER", "ADMIN"):
            self.assertTrue(values[f"E2E_{prefix}_PASSWORD"])
            self.assertNotIn(values[f"E2E_{prefix}_PASSWORD"], output)                  # never printed
            token = self.login(values[f"E2E_{prefix}_EMAIL"], values[f"E2E_{prefix}_PASSWORD"])
            self.assertTrue(token)
        self.assertEqual(len({values[f"E2E_{p}_PASSWORD"] for p in ("CUSTOMER", "SELLER", "ADMIN")}), 3)

    def test_the_seller_can_use_the_whole_portal_but_not_owner_only_things(self):
        self.run_command(self.env)
        values = env_values(self.env)
        token = self.login(values["E2E_SELLER_EMAIL"], values["E2E_SELLER_PASSWORD"])
        self.assertEqual(self.get("/api/v1/seller/products", token).status_code, 200)
        self.assertEqual(self.get("/api/v1/seller/dashboard", token).status_code, 200)
        self.assertEqual(self.get("/api/v1/seller/team", token).status_code, 403)            # owner only
        member = StoreMember.objects.get(email="fe-seller@asknget.test")
        self.assertTrue(all(level == "manage" for level in member.permissions.values()))

    def test_the_admin_is_staff_but_not_a_superuser_and_the_customer_is_neither(self):
        self.run_command(self.env)
        admin, customer, seller = (User.objects.get(email=f"fe-{n}@asknget.test") for n in ("admin", "customer", "seller"))
        self.assertEqual((admin.is_staff, admin.is_superuser), (True, False))
        self.assertEqual((customer.is_staff, customer.is_seller), (False, False))
        values = env_values(self.env)
        token = self.login(values["E2E_ADMIN_EMAIL"], values["E2E_ADMIN_PASSWORD"])
        self.assertEqual(self.get("/api/v1/admin/orders", token).status_code, 200)

    def test_running_it_again_rotates_the_passwords_and_keeps_other_lines(self):
        self.env.write_text("API_ORIGIN=https://example.test\nMY_OWN=keep\n", encoding="utf-8")
        self.run_command(self.env)
        first = env_values(self.env)
        self.run_command(self.env)
        second = env_values(self.env)
        self.assertEqual((second["API_ORIGIN"], second["MY_OWN"]), ("https://example.test", "keep"))
        self.assertNotEqual(first["E2E_SELLER_PASSWORD"], second["E2E_SELLER_PASSWORD"])
        with self.assertRaises(AssertionError):                                           # the old password no longer logs in
            self.login(first["E2E_SELLER_EMAIL"], first["E2E_SELLER_PASSWORD"])
        self.assertEqual(StoreMember.objects.filter(email="fe-seller@asknget.test").count(), 1)
        self.assertEqual(self.env.read_text(encoding="utf-8").count("# Test accounts"), 1)

    def test_a_byte_order_mark_from_windows_does_not_corrupt_the_first_variable(self):
        self.env.write_bytes(b"\xef\xbb\xbfAPI_ORIGIN=https://example.test\n")
        self.run_command(self.env)
        raw = self.env.read_bytes()
        self.assertNotIn(b"\xef\xbb\xbf", raw)
        self.assertEqual(env_values(self.env)["API_ORIGIN"], "https://example.test")

    def test_it_needs_the_demo_store(self):
        self.store.slug = "something-else"
        self.store.save(update_fields=["slug"])
        with self.assertRaises(CommandError):
            self.run_command(self.env)

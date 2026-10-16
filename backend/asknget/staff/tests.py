"""Staff roles with dynamic permissions, per market (sprint 9, phase 1)."""

from io import StringIO

from django.contrib.auth.models import Group, Permission
from django.core.management import call_command
from django.test import TestCase

from asknget.api import api
from catalog.models import CategoryRequest
from core.auth import StaffAuth
from core.testing import ApiTestCase, make_category, make_country, make_product, make_staff, make_store, make_user

from .permissions import CATALOGUE

NO_PERMISSION_NEEDED = {"/api/v1/admin/me"}       # says what the caller may do; any staff member may ask


def admin_operations():
    """(path, method, operation) for every /admin operation, as the API serves it."""
    for bound in api._get_bound_routers():
        for path, view in bound.path_operations.items():
            full = "/api/v1/" + "/".join(p.strip("/") for p in (bound.prefix, path) if p.strip("/"))
            if full.startswith("/api/v1/admin"):
                for op in view.operations:
                    for method in op.methods:
                        yield full, method, op


class CatalogueTests(TestCase):
    def test_every_admin_operation_names_a_permission_from_the_catalogue(self):
        seen, missing = 0, []
        for path, method, op in admin_operations():
            seen += 1
            perms = [cb.permission for cb in op.auth_callbacks if isinstance(cb, StaffAuth)]
            if path in NO_PERMISSION_NEEDED:
                self.assertTrue(perms == [None], path)                    # staff only, still
                continue
            if not perms or not all(perms):
                missing.append(f"{method} {path}")
            for perm in perms:
                if perm:
                    self.assertIn(perm, CATALOGUE, f"{method} {path}")
        self.assertGreaterEqual(seen, 39)
        self.assertEqual(missing, [], "Give each /admin operation auth=StaffAuth('<section>.<action>')")

    def test_the_catalogue_and_the_database_permissions_match_so_no_migration_is_missing(self):
        call_command("makemigrations", "staff", "--check", "--dry-run", stdout=StringIO())      # exits non-zero on a missing migration
        codes = set(Permission.objects.filter(content_type__app_label="staff").exclude(codename__endswith="_staffprofile").values_list("codename", flat=True))
        self.assertEqual(codes, set(CATALOGUE))

    def test_the_starter_roles_exist_and_the_administrator_has_everything(self):
        roles = {g.name: set(g.permissions.values_list("codename", flat=True)) for g in Group.objects.all()}
        self.assertEqual(roles["Administrator"], set(CATALOGUE))
        self.assertEqual(roles["KYC reviewer"], {"sellers.view", "sellers.review"})
        self.assertTrue({"Catalogue", "Support", "Finance"} <= set(roles))


class PermissionTests(ApiTestCase):
    @classmethod
    def setUpTestData(cls):
        cls.beauty = make_category("Beauty")
        cls.store = make_store()
        cls.cream = make_product(cls.store, cls.beauty, "BB Cream", 2599, 4099, stock=5)

    def test_a_role_grants_exactly_its_permissions(self):
        token = self.token_for(make_staff("products.view"))
        self.assertEqual(self.get("/api/v1/admin/products", token, status="all").status_code, 200)
        denied = self.post(f"/api/v1/admin/products/{self.cream.pk}/approve", token=token)
        self.assertEqual((denied.status_code, denied.json()["detail"]), (403, "Missing permission: products.review"))
        self.assertEqual(self.get("/api/v1/admin/orders", token).status_code, 403)

    def test_staff_without_a_role_can_do_nothing_but_ask_what_they_may_do(self):
        token = self.token_for(make_user(is_staff=True))
        self.assertEqual(self.get("/api/v1/admin/products", token).status_code, 403)
        me = self.get("/api/v1/admin/me", token).json()
        self.assertEqual(me, {"is_superuser": False, "permissions": [], "markets": None})

    def test_changing_a_role_takes_effect_on_the_next_request(self):
        user = make_staff("orders.view")
        token = self.token_for(user)
        self.assertEqual(self.get("/api/v1/admin/products", token).status_code, 403)
        user.groups.first().permissions.add(Permission.objects.get(codename="products.view"))
        self.assertEqual(self.get("/api/v1/admin/products", token).status_code, 200)

    def test_a_superuser_and_the_administrator_role_can_do_everything_and_customers_nothing(self):
        boss = self.token_for(make_user(is_staff=True, is_superuser=True))
        self.assertEqual(self.get("/api/v1/admin/me", boss).json()["permissions"], sorted(CATALOGUE))
        self.assertEqual(self.get("/api/v1/admin/products", self.token_for(make_staff())).status_code, 200)
        self.assertEqual(self.get("/api/v1/admin/products", self.token_for(make_user())).status_code, 403)
        self.assertEqual(self.get("/api/v1/admin/me").status_code, 401)


class MarketScopeTests(ApiTestCase):
    @classmethod
    def setUpTestData(cls):
        make_country("PK")
        cls.beauty = make_category("Beauty")
        cls.za_store = make_store(name="Cape Glow")
        cls.pk_store = make_store(name="Lahore Looks", country="PK")
        cls.za_cream = make_product(cls.za_store, cls.beauty, "ZA Cream", 2599, 4099, stock=5)
        cls.pk_cream = make_product(cls.pk_store, cls.beauty, "PK Cream", 2599, 4099, stock=5)
        cls.za_staff = make_staff(markets=("ZA",))
        cls.pk_request = CategoryRequest.objects.create(store=cls.pk_store, name="Attar", reason="Perfume oils")

    def setUp(self):
        self.token = self.token_for(self.za_staff)

    def test_lists_show_only_the_staff_members_markets(self):
        names = {p["name"] for p in self.get("/api/v1/admin/products", self.token, status="all").json()["items"]}
        self.assertEqual(names, {"ZA Cream"})
        apps = {a["store_display_name"] for a in self.get("/api/v1/admin/seller-applications", self.token).json()["items"]}
        self.assertEqual(apps, {"Cape Glow"})
        self.assertEqual(self.get("/api/v1/admin/category-requests", self.token).json()["items"], [])
        self.assertEqual([p["country"] for p in self.get("/api/v1/admin/returns/policies", self.token).json()], ["ZA"])
        self.assertEqual(self.get("/api/v1/admin/me", self.token).json()["markets"], ["ZA"])

    def test_another_markets_record_does_not_exist_for_them(self):
        self.assertEqual(self.post(f"/api/v1/admin/products/{self.pk_cream.pk}/approve", token=self.token).status_code, 404)
        self.assertEqual(self.post(f"/api/v1/admin/stores/{self.pk_store.pk}/plan", {"is_premium": True}, token=self.token).status_code, 404)
        self.assertEqual(self.get(f"/api/v1/admin/seller-applications/{self.pk_store.application_id}", self.token).status_code, 404)
        self.assertEqual(self.get(f"/api/v1/admin/category-requests/{self.pk_request.pk}", self.token).status_code, 404)
        self.assertEqual(self.post(f"/api/v1/admin/category-requests/{self.pk_request.pk}/reject", {"reason": "Not a category we sell"}, token=self.token).status_code, 404)
        self.assertEqual(self.get(f"/api/v1/admin/ledger/stores/{self.pk_store.pk}", self.token).status_code, 404)
        self.assertEqual(self.put("/api/v1/admin/returns/policies/PK", {"is_active": True, "window_days": 7, "defect_days": 7, "restocking_fee_bps": 0}, token=self.token).status_code, 404)
        self.assertEqual(self.get(f"/api/v1/admin/ledger/stores/{self.za_store.pk}", self.token).status_code, 200)

    def test_settings_shared_by_every_market_need_staff_without_a_market_limit(self):
        reason = {"code": "smells-odd", "label": {"en": "Smells odd"}, "kind": "defect"}
        self.assertEqual(self.post("/api/v1/admin/returns/reasons", reason, token=self.token).status_code, 403)
        rule = {"category_id": str(self.beauty.pk), "change_of_mind_days": 7}
        self.assertEqual(self.post("/api/v1/admin/returns/rules", rule, token=self.token).status_code, 403)          # every market
        self.assertEqual(self.post("/api/v1/admin/returns/rules", {**rule, "country": "PK"}, token=self.token).status_code, 404)
        self.assertEqual(self.post("/api/v1/admin/returns/rules", {**rule, "country": "ZA"}, token=self.token).status_code, 201)
        unlimited = self.token_for(make_staff())
        self.assertEqual(self.post("/api/v1/admin/returns/reasons", reason, token=unlimited).status_code, 201)

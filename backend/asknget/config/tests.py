"""Admin-managed configuration: settings resolve most-specific-first and cannot hold
an invalid value; commission rules pick the right winner and never apply early."""

from datetime import timedelta
from io import StringIO

from django.conf import settings as django_settings
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.db import IntegrityError, transaction
from django.test import TestCase, override_settings
from django.utils import timezone

from config import registry, services
from config.admin import SettingForm
from config.models import CommissionRule, Setting
from core.testing import make_category, make_country, make_store


class RegistryTests(TestCase):
    def test_every_default_satisfies_its_own_definition(self):
        for definition in registry.REGISTRY.values():
            definition.validate(definition.default)

    def test_an_unknown_key_fails_loudly_on_read(self):
        with self.assertRaises(registry.UnknownSetting):
            services.get("payouts.hold_dayz")

    def test_types_and_bounds(self):
        days = registry.definition("payouts.hold_days")
        self.assertEqual(days.validate(14), 14)
        for bad in ("14", 14.5, True, -1, 366, None):
            with self.assertRaises(ValueError, msg=repr(bad)):
                days.validate(bad)
        with self.assertRaises(ValueError):
            registry.definition("payouts.hold_mode").validate("forever")


class ResolutionTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.za_store = make_store(name="Huda Beauty")
        cls.pk = make_country("PK")
        cls.pk_store = make_store(name="Karachi Cosmetics", country=cls.pk)

    def put(self, value, scope="global", **target):
        return Setting.objects.create(key="payouts.hold_days", scope=scope, value=value, **target)

    def test_the_code_default_applies_when_nothing_is_set(self):
        self.assertEqual(services.get("payouts.hold_days", store=self.za_store), 7)
        self.assertEqual(services.resolve(["payouts.hold_days"])["payouts.hold_days"], (7, "default"))

    def test_most_specific_wins_store_then_market_then_global(self):
        self.put(10)
        self.assertEqual(services.get("payouts.hold_days", store=self.za_store), 10)
        self.put(5, "country", country=self.za_store.country)
        self.assertEqual(services.get("payouts.hold_days", store=self.za_store), 5)
        self.put(2, "store", store=self.za_store)
        self.assertEqual(services.get("payouts.hold_days", store=self.za_store), 2)
        self.assertEqual(services.resolve(["payouts.hold_days"], store=self.za_store)["payouts.hold_days"], (2, "store"))

    def test_another_store_or_market_is_unaffected(self):
        self.put(2, "store", store=self.za_store)
        self.put(5, "country", country=self.za_store.country)
        self.assertEqual(services.get("payouts.hold_days", store=self.pk_store), 7)          # PK has no override
        self.assertEqual(services.get("payouts.hold_days", country="PK"), 7)
        self.assertEqual(services.get("payouts.hold_days", country=self.za_store.country_id), 5)

    def test_deleting_an_override_restores_the_default(self):
        row = self.put(30)
        self.assertEqual(services.get("payouts.hold_days"), 30)
        row.delete()
        self.assertEqual(services.get("payouts.hold_days"), 7)

    def test_an_edit_applies_on_the_next_read(self):
        row = self.put(30)
        row.value = 3
        row.save()
        self.assertEqual(services.get("payouts.hold_days"), 3)

    def test_many_keys_cost_one_query(self):
        with self.assertNumQueries(1):
            services.get_many(["payouts.hold_mode", "payouts.hold_days", "payouts.minimum_cents"], store=self.za_store)


class ValidationTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.store = make_store()

    def test_an_invalid_value_cannot_be_saved_by_any_route(self):
        with self.assertRaisesMessage(ValidationError, "whole number"):
            Setting.objects.create(key="payouts.hold_days", value="seven")
        with self.assertRaisesMessage(ValidationError, "at most 365"):
            Setting.objects.create(key="payouts.hold_days", value=9999)

    def test_an_unregistered_key_is_refused(self):
        with self.assertRaisesMessage(ValidationError, "not a registered setting"):
            Setting.objects.create(key="payouts.nope", value=1)

    def test_a_scope_the_setting_does_not_allow_is_refused(self):
        # the minimum payout is a platform/market policy, not something a single store sets for itself
        with self.assertRaisesMessage(ValidationError, "can only be set for"):
            Setting.objects.create(key="payouts.minimum_cents", scope="store", store=self.store, value=100)

    def test_scope_and_target_must_agree(self):
        with self.assertRaises(ValidationError):
            Setting.objects.create(key="payouts.hold_days", scope="store", value=3)                       # no store
        with self.assertRaises(ValidationError):
            Setting.objects.create(key="payouts.hold_days", scope="global", store=self.store, value=3)    # stray store

    def test_the_database_enforces_it_too(self):
        # bypass save() / clean(): the constraints are the last line of defence
        with self.assertRaises(IntegrityError), transaction.atomic():
            Setting.objects.bulk_create([Setting(key="payouts.hold_days", scope="store", value=3)])

    def test_one_override_per_key_and_target(self):
        Setting.objects.create(key="payouts.hold_days", value=3)
        with self.assertRaises(IntegrityError), transaction.atomic():
            Setting.objects.bulk_create([Setting(key="payouts.hold_days", scope="global", value=4)])

    def test_the_admin_form_offers_only_registered_keys(self):
        keys = [value for value, _ in SettingForm().fields["key"].choices if value]
        self.assertEqual(sorted(keys), sorted(registry.REGISTRY))
        form = SettingForm(data={"key": "payouts.hold_days", "scope": "global", "value": "5000"})
        self.assertFalse(form.is_valid())
        self.assertIn("value", form.errors)


class CommissionTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.store = make_store(name="Huda Beauty")
        cls.other = make_store(name="Other Co")
        cls.pk = make_country("PK")
        cls.pk_store = make_store(name="Karachi Cosmetics", country=cls.pk)
        cls.beauty = make_category("Beauty")
        cls.skincare = make_category("Skincare", parent=cls.beauty)
        cls.serum = make_category("Serums", parent=cls.skincare)
        cls.tech = make_category("Tech")

    def rate(self, store, category, **kw):
        return services.commission_rates(store, [category.pk if category else None], **kw)[category.pk if category else None]

    def rule(self, scope, bps, **target):
        return CommissionRule.objects.create(scope=scope, rate_bps=bps, **target)

    def test_a_global_starting_rate_is_seeded_and_applies_everywhere(self):
        rate = self.rate(self.store, self.tech)
        self.assertEqual((rate.bps, rate.percent), (500, 5))
        self.assertIsNotNone(rate.rule_id)

    def test_most_specific_wins_store_over_category_over_market_over_global(self):
        self.rule("country", 700, country=self.store.country)
        self.assertEqual(self.rate(self.store, self.tech).bps, 700)
        self.rule("category", 900, category=self.tech)
        self.assertEqual(self.rate(self.store, self.tech).bps, 900)
        self.rule("store", 300, store=self.store)
        self.assertEqual(self.rate(self.store, self.tech).bps, 300)
        self.assertEqual(self.rate(self.other, self.tech).bps, 900)          # a different store keeps the category rate

    def test_a_category_rule_covers_its_subcategories_and_the_nearest_one_wins(self):
        self.rule("category", 1000, category=self.beauty)
        self.assertEqual(self.rate(self.store, self.serum).bps, 1000)        # two levels down
        self.rule("category", 1400, category=self.skincare)
        self.assertEqual(self.rate(self.store, self.serum).bps, 1400)        # the closer ancestor
        self.rule("category", 1800, category=self.serum)
        self.assertEqual(self.rate(self.store, self.serum).bps, 1800)        # its own
        self.assertEqual(self.rate(self.store, self.beauty).bps, 1000)       # parent unaffected by children

    def test_markets_do_not_leak(self):
        self.rule("country", 700, country=self.pk)
        self.assertEqual(self.rate(self.pk_store, self.tech).bps, 700)
        self.assertEqual(self.rate(self.store, self.tech).bps, 500)

    def test_a_scheduled_rate_changes_nothing_until_its_time(self):
        future = self.rule("global", 800)
        future.effective_from = timezone.now() + timedelta(days=3)
        future.save()
        self.assertEqual(self.rate(self.store, self.tech).bps, 500)
        self.assertEqual(self.rate(self.store, self.tech, at=timezone.now() + timedelta(days=4)).bps, 800)

    def test_the_latest_started_rule_at_a_level_wins(self):
        old = self.rule("global", 500)
        old.effective_from = timezone.now() - timedelta(days=30)
        old.save()
        self.rule("global", 650)                                                # starts now
        self.assertEqual(self.rate(self.store, self.tech).bps, 650)
        # asked about last week, the old rate applied
        self.assertEqual(self.rate(self.store, self.tech, at=timezone.now() - timedelta(days=7)).bps, 500)

    def test_an_inactive_rule_is_ignored(self):
        off = self.rule("store", 100, store=self.store)
        off.is_active = False
        off.save()
        self.assertEqual(self.rate(self.store, self.tech).bps, 500)

    def test_no_rule_at_all_means_zero_and_says_so(self):
        CommissionRule.objects.all().delete()
        rate = self.rate(self.store, self.tech)
        self.assertEqual((rate.bps, rate.rule_id), (0, None))

    def test_a_line_with_no_category_still_gets_the_store_market_or_global_rate(self):
        self.rule("country", 700, country=self.store.country)
        self.assertEqual(self.rate(self.store, None).bps, 700)

    def test_several_categories_resolve_together_cheaply(self):
        self.rule("category", 1000, category=self.beauty)
        # One query per level of category depth (here 2), never per product, plus one for the rules.
        with self.assertNumQueries(3):
            rates = services.commission_rates(self.store, [self.serum.pk, self.tech.pk, self.beauty.pk])
        self.assertEqual({k: v.bps for k, v in rates.items()}, {self.serum.pk: 1000, self.tech.pk: 500, self.beauty.pk: 1000})

    def test_a_rule_must_target_exactly_what_its_scope_says(self):
        with self.assertRaises(ValidationError):
            CommissionRule(scope="store", rate_bps=100).full_clean()
        with self.assertRaises(ValidationError):
            CommissionRule(scope="global", rate_bps=100, store=self.store).full_clean()
        with self.assertRaises(ValidationError):
            CommissionRule(scope="global", rate_bps=10001).full_clean()          # over 100 %
        with self.assertRaises(IntegrityError), transaction.atomic():
            CommissionRule.objects.bulk_create([CommissionRule(scope="store", rate_bps=100)])


class ShowSettingsCommandTests(TestCase):
    def test_it_reports_the_effective_value_and_where_it_came_from(self):
        store = make_store(name="Huda Beauty")
        Setting.objects.create(key="payouts.hold_days", scope="store", store=store, value=3)
        out = StringIO()
        call_command("show_settings", store=store.slug, stdout=out)
        text = out.getvalue()
        self.assertRegex(text, r"payouts\.hold_days\s+3\s+store")
        self.assertRegex(text, r"payouts\.hold_mode\s+'return_window'\s+default")
        self.assertIn("commission", text)
        self.assertIn(django_settings.DEFAULT_COUNTRY, text)


@override_settings(STORAGES={
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    # production hashes static files at `collectstatic`; the test database has no manifest to read
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
})
class AdminPagesTests(TestCase):
    """The admin is how an owner changes any of this, so its pages must actually load and save."""

    @classmethod
    def setUpTestData(cls):
        from core.testing import make_user

        cls.staff = make_user(is_staff=True, is_superuser=True)
        cls.store = make_store(name="Huda Beauty")

    def setUp(self):
        self.client.force_login(self.staff)

    def test_the_pages_render(self):
        for name in ("setting_changelist", "setting_add", "commissionrule_changelist", "commissionrule_add"):
            res = self.client.get(f"/admin/config/{name.split('_')[0]}/" + ("add/" if name.endswith("add") else ""))
            self.assertEqual(res.status_code, 200, name)

    def test_saving_a_setting_through_admin_records_who_and_validates(self):
        data = {"key": "payouts.hold_days", "scope": "global", "value": "14", "note": "agreed with finance"}
        res = self.client.post("/admin/config/setting/add/", data)
        self.assertEqual(res.status_code, 302, getattr(res, "context", None) and res.context["adminform"].form.errors)
        row = Setting.objects.get(key="payouts.hold_days")
        self.assertEqual((row.value, row.updated_by_id), (14, self.staff.pk))
        bad = self.client.post("/admin/config/setting/add/", {**data, "scope": "store", "value": "14"})
        self.assertEqual(bad.status_code, 200)                                  # form re-shown with an error
        self.assertEqual(Setting.objects.filter(key="payouts.hold_days").count(), 1)

    def test_adding_a_commission_rule_through_admin(self):
        res = self.client.post("/admin/config/commissionrule/add/", {
            "scope": "store", "store": self.store.pk, "rate_bps": 250,
            "effective_from_0": "2026-10-01", "effective_from_1": "00:00:00", "is_active": "on", "note": "premium deal",
        })
        self.assertEqual(res.status_code, 302, getattr(res, "context", None) and res.context["adminform"].form.errors)
        self.assertEqual(CommissionRule.objects.get(scope="store").rate_bps, 250)

"""Region resolution, language / timezone activation, i18n fallback."""

from django.conf import settings
from django.core.exceptions import ValidationError
from django.test import override_settings
from django.utils import timezone, translation

from core.testing import ApiTestCase

from . import context
from .i18n import pick
from .models import Country, KycPolicy, ReturnPolicy


def make_pk():
    pk = Country.objects.create(code="PK", name="Pakistan", currency="PKR", timezone="Asia/Karachi",
                                default_language="en", languages=["en", "ur"], phone_prefix="+92", sort_order=2)
    ReturnPolicy.objects.create(country=pk, window_days=7, summary={"en": "7 days", "ur": "۷ دن"})
    KycPolicy.objects.create(country=pk, id_number_label="CNIC / Passport number")
    return pk


class ModelTests(ApiTestCase):
    def test_default_country_exists_from_migration(self):
        default = Country.objects.get(is_default=True)
        self.assertEqual(default.code, settings.DEFAULT_COUNTRY)
        self.assertEqual(default.return_window_days, 30)

    def test_only_one_default(self):
        pk = make_pk()
        pk.is_default = True
        pk.save()
        self.assertEqual(list(Country.objects.filter(is_default=True).values_list("code", flat=True)), ["PK"])

    def test_validation(self):
        c = Country(code="xx", name="X", currency="abc", timezone="Mars/Olympus", default_language="en")
        with self.assertRaises(ValidationError) as ctx:
            c.full_clean()
        self.assertIn("timezone", ctx.exception.message_dict)
        c.timezone = "UTC"
        c.languages = ["en", "klingon"]
        with self.assertRaises(ValidationError) as ctx:
            c.full_clean()
        self.assertIn("languages", ctx.exception.message_dict)
        c.languages = ["ur"]
        with self.assertRaises(ValidationError) as ctx:
            c.full_clean()
        self.assertIn("default_language", ctx.exception.message_dict)
        c.languages = ["en"]
        c.full_clean()
        c.save()
        self.assertEqual((c.code, c.currency, c.subdomain), ("XX", "ABC", "xx"))

    def test_i18n_pick_fallbacks(self):
        pk = make_pk()
        m = {"en": "Hello", "ur": "سلام"}
        self.assertEqual(pick(m, "ur", pk), "سلام")
        self.assertEqual(pick(m, "ur-PK", pk), "سلام")
        self.assertEqual(pick(m, "zu", pk), "Hello")  # not offered → country default
        self.assertEqual(pick({"af": "Hallo"}, "ur", pk), "Hallo")  # last resort: first value
        self.assertEqual(pick({}, "ur", pk, default="-"), "-")
        with context.region(pk, language="ur"):
            self.assertEqual(pick(m), "سلام")


class ResolutionTests(ApiTestCase):
    @classmethod
    def setUpTestData(cls):
        cls.pk = make_pk()

    def test_default_when_nothing_is_sent(self):
        res = self.get("/api/v1/regions/current")
        self.assertEqual(res.status_code, 200, res.content)
        body = res.json()
        self.assertEqual(body["country"]["code"], settings.DEFAULT_COUNTRY)
        self.assertEqual(body["language"], "en")
        self.assertEqual(body["currency"], body["country"]["currency"])
        self.assertEqual(body["return_policy"]["window_days"], 30)
        self.assertEqual(res.headers["X-Region"], settings.DEFAULT_COUNTRY)
        self.assertEqual(res.headers["Content-Language"], "en")
        self.assertIn("X-Region", res.headers["Vary"])

    def test_explicit_header_wins(self):
        body = self.get("/api/v1/regions/current", region="pk").json()
        self.assertEqual(body["country"]["code"], "PK")
        self.assertEqual(body["timezone"], "Asia/Karachi")
        self.assertEqual(body["currency"], "PKR")
        self.assertEqual(body["return_policy"], {"window_days": 7, "defect_days": 30, "restocking_fee_bps": 0, "summary": "7 days", "terms": "", "url": ""})

    def test_query_param(self):
        self.assertEqual(self.client.get("/api/v1/regions/current?region=PK").json()["country"]["code"], "PK")

    def test_unknown_explicit_region_is_400(self):
        res = self.get("/api/v1/regions/current", region="XX")
        self.assertEqual(res.status_code, 400)
        self.assertEqual(res.json(), {"detail": "unknown_region"})

    def test_inactive_country_is_unknown(self):
        Country.objects.filter(pk="PK").update(is_active=False)
        self.assertEqual(self.get("/api/v1/regions/current", region="PK").status_code, 400)
        self.assertNotIn("PK", [c["code"] for c in self.get("/api/v1/regions").json()])

    @override_settings(ALLOWED_HOSTS=[".testserver", "testserver"])
    def test_subdomain(self):
        res = self.client.get("/api/v1/regions/current", HTTP_HOST="pk.testserver")
        self.assertEqual(res.json()["country"]["code"], "PK")
        res = self.client.get("/api/v1/regions/current", HTTP_HOST="pk.api.testserver:8080")
        self.assertEqual(res.json()["country"]["code"], "PK")
        # unknown label (container name, localhost) falls through to the default
        res = self.client.get("/api/v1/regions/current", HTTP_HOST="web.testserver")
        self.assertEqual(res.json()["country"]["code"], settings.DEFAULT_COUNTRY)
        # explicit header beats the host
        res = self.client.get("/api/v1/regions/current", HTTP_HOST="pk.testserver", headers={"X-Region": settings.DEFAULT_COUNTRY})
        self.assertEqual(res.json()["country"]["code"], settings.DEFAULT_COUNTRY)

    def test_language_from_accept_language_within_country_list(self):
        res = self.client.get("/api/v1/regions/current", headers={"X-Region": "PK", "Accept-Language": "fr-FR,ur-PK;q=0.8,en;q=0.5"})
        self.assertEqual(res.json()["language"], "ur")
        self.assertEqual(res.headers["Content-Language"], "ur")
        # a language the country does not offer → country default
        res = self.client.get("/api/v1/regions/current", headers={"X-Region": "PK", "Accept-Language": "zu"})
        self.assertEqual(res.json()["language"], "en")
        # explicit header wins over Accept-Language
        res = self.client.get("/api/v1/regions/current", headers={"X-Region": "PK", "X-Language": "ur", "Accept-Language": "en"})
        self.assertEqual(res.json()["language"], "ur")
        # policy text follows the language
        self.assertEqual(res.json()["return_policy"]["summary"], "۷ دن")

    def test_timezone_header_must_be_valid(self):
        res = self.client.get("/api/v1/regions/current", headers={"X-Region": "PK", "X-Timezone": "Europe/London"})
        self.assertEqual(res.json()["timezone"], "Europe/London")
        res = self.client.get("/api/v1/regions/current", headers={"X-Region": "PK", "X-Timezone": "Mars/Olympus"})
        self.assertEqual(res.json()["timezone"], "Asia/Karachi")

    def test_context_is_reset_after_the_request(self):
        self.get("/api/v1/regions/current", region="PK")
        self.assertIsNone(context.get_country())
        self.assertEqual(timezone.get_current_timezone_name(), "UTC")
        self.assertEqual(translation.get_language(), settings.LANGUAGE_CODE)

    def test_non_api_paths_are_not_resolved(self):
        res = self.client.get("/not-the-api/", headers={"X-Region": "XX"})
        self.assertEqual(res.status_code, 404)  # not the middleware's 400

    def test_regions_list(self):
        body = self.get("/api/v1/regions").json()
        self.assertEqual([c["code"] for c in body], [settings.DEFAULT_COUNTRY, "PK"])
        self.assertEqual(body[1]["languages"], ["en", "ur"])
        self.assertEqual(body[1]["subdomain"], "pk")

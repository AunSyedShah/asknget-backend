"""`manage.py set_markets`: which markets are live."""

from io import StringIO

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase

from core.testing import ApiTestCase, make_country
from markets.models import Country


def run(*codes):
    out = StringIO()
    call_command("set_markets", *codes, stdout=out)
    return out.getvalue()


class SetMarketsTests(TestCase):
    def setUp(self):
        make_country("PK")

    def test_only_the_listed_market_stays_live_and_becomes_the_default(self):
        run("ZA")
        za, pk = Country.objects.get(code="ZA"), Country.objects.get(code="PK")
        self.assertEqual((za.is_active, za.is_default, pk.is_active, pk.is_default), (True, True, False, False))

    def test_several_markets_can_be_live_and_the_first_is_the_default(self):
        run("PK", "ZA")
        za, pk = Country.objects.get(code="ZA"), Country.objects.get(code="PK")
        self.assertEqual((za.is_active, pk.is_active, za.is_default, pk.is_default), (True, True, False, True))

    def test_codes_are_case_insensitive_and_nothing_is_deleted(self):
        run("za")
        self.assertEqual(Country.objects.count(), 2)

    def test_an_unknown_code_changes_nothing(self):
        with self.assertRaises(CommandError):
            run("ZA", "XX")
        self.assertTrue(Country.objects.get(code="PK").is_active)


class SwitchedOffMarketTests(ApiTestCase):
    def test_a_switched_off_market_is_an_unknown_region_and_the_default_still_works(self):
        make_country("PK")
        run("ZA")
        res = self.get("/api/v1/regions", region="PK")
        self.assertEqual((res.status_code, res.json()), (400, {"detail": "unknown_region"}))
        listed = self.get("/api/v1/regions", region="ZA").json()
        self.assertEqual([c["code"] for c in listed], ["ZA"])
        self.assertEqual(self.get("/api/v1/regions").status_code, 200)            # no header: the default market

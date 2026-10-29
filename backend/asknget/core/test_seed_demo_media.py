"""seed_demo_media, the storage that serves its links, and per-market contact details."""

from io import StringIO

from django.core.files.storage import default_storage
from django.core.management import call_command
from django.core.management.base import CommandError

from catalog.models import Category, ProductImage
from core.testing import ApiTestCase, make_category, make_country, make_product, make_store
from marketing.models import Banner
from markets.models import Country


class StorageTests(ApiTestCase):
    def test_a_full_web_address_is_served_as_it_is_and_a_file_name_still_gets_the_media_prefix(self):
        self.assertEqual(default_storage.url("https://example.test/a.png"), "https://example.test/a.png")
        self.assertEqual(default_storage.url("products/a.png"), "/media/products/a.png")


class SeedDemoMediaTests(ApiTestCase):
    @classmethod
    def setUpTestData(cls):
        cls.beauty = make_category("Beauty")
        cls.store = make_store()
        cls.cream = make_product(cls.store, cls.beauty, "BB Cream", 2599, 4099, stock=5)

    def run_it(self):
        out = StringIO()
        call_command("seed_demo_media", "--yes-this-is-staging", stdout=out)
        return out.getvalue()

    def test_it_refuses_without_the_staging_flag(self):
        with self.assertRaises(CommandError):
            call_command("seed_demo_media", stdout=StringIO())
        self.assertEqual(ProductImage.objects.count(), 0)

    def test_products_and_categories_get_pictures_the_api_shows_as_full_addresses(self):
        self.run_it()
        listed = self.get("/api/v1/products").json()["items"]
        self.assertTrue(listed)
        self.assertTrue(all(p["image_url"].startswith("https://picsum.photos/") for p in listed))
        detail = self.get(f"/api/v1/products/{self.cream.slug}").json()
        self.assertEqual(len(detail["images"]), 3)
        self.assertEqual(sum(1 for i in detail["images"] if i.get("is_primary")), 1)
        tree = self.get("/api/v1/categories").json()
        self.assertTrue(tree[0]["image_url"].startswith("https://picsum.photos/"))

    def test_the_landing_page_has_five_hero_slides_and_four_tiles_in_every_market(self):
        make_country("PK")
        self.run_it()
        for region in ("ZA", "PK"):
            hero = self.get("/api/v1/banners", region=region, placement="hero").json()
            tiles = self.get("/api/v1/banners", region=region, placement="top_discount").json()
            self.assertEqual((len(hero), len(tiles)), (5, 4), region)
            self.assertEqual(hero[0]["title"], "Carry What Matters")
            self.assertTrue(hero[0]["image_url"].startswith("https://"))
        self.assertEqual(self.get("/api/v1/banners", placement="nonsense").status_code, 422)     # placement is a declared enum

    def test_running_it_again_adds_nothing_and_changes_no_picture(self):
        first = self.run_it()
        names = sorted(ProductImage.objects.values_list("image", flat=True))
        banners = Banner.objects.count()
        second = self.run_it()
        self.assertIn("0 product pictures, 0 category pictures, 0 banners", second)
        self.assertEqual(sorted(ProductImage.objects.values_list("image", flat=True)), names)
        self.assertEqual(Banner.objects.count(), banners)
        self.assertIn("3 product pictures", first)

    def test_a_picture_that_already_exists_is_left_alone(self):
        category = Category.objects.get(slug="beauty")
        category.image = "categories/mine.png"
        category.save()
        self.run_it()
        category.refresh_from_db()
        self.assertEqual(category.image.name, "categories/mine.png")


class SiteInfoTests(ApiTestCase):
    def test_each_market_shows_its_own_contact_details_and_falls_back_to_the_platform_wide_ones(self):
        from core.models import SiteSetting

        SiteSetting.objects.create(key="contact_phone", value="+1 000 000")
        SiteSetting.objects.create(key="contact_email", value="hello@asknget.test")
        za = Country.objects.get(code="ZA")
        za.site_info = {"contact_phone": "+27 11 555 0123", "address": "12 Rivonia Road, Sandton", "social": {"facebook": "https://facebook.com/x"}}
        za.save()
        pk = make_country("PK")
        mine = self.get("/api/v1/site-info", region="ZA").json()
        self.assertEqual((mine["contact_phone"], mine["address"], mine["social"]), ("+27 11 555 0123", "12 Rivonia Road, Sandton", {"facebook": "https://facebook.com/x"}))
        self.assertEqual(mine["contact_email"], "hello@asknget.test")                       # not set for ZA: the platform-wide one
        other = self.get("/api/v1/site-info", region=pk.code).json()
        self.assertEqual((other["contact_phone"], other["address"], other["social"]), ("+1 000 000", "", {}))

    def test_a_market_cannot_be_saved_with_unknown_contact_keys(self):
        from django.core.exceptions import ValidationError

        za = Country.objects.get(code="ZA")
        za.site_info = {"fax": "123"}
        with self.assertRaises(ValidationError):
            za.full_clean()

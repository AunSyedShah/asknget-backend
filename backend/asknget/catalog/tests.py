"""End-to-end tests for the public catalog, reviews and wishlist."""

import tempfile
from io import BytesIO

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings
from PIL import Image

from core.testing import ApiTestCase, make_category, make_product, make_store, make_user
from sellers.models import Store

from .models import Brand, Product, Review
from .services import refresh_category_counts, refresh_product_rating


@override_settings(MEDIA_ROOT=tempfile.mkdtemp())
class CatalogTestCase(ApiTestCase):
    def setUp(self):
        self.store = make_store()
        self.beauty = make_category("Beauty")
        self.makeup = make_category("Makeup", parent=self.beauty)
        self.fashion = make_category("Fashion")
        self.nars = Brand.objects.create(name="NARS", slug="nars")
        self.zara = Brand.objects.create(name="ZARA", slug="zara")
        self.bb = make_product(self.store, self.makeup, "NARS BB Cream", 2599, 4099, brand=self.nars,
                               options={"Size": ["15 ml", "30 ml"], "Shade": ["Ivory", "Natural"]},
                               is_featured=True, short_description="Lightweight, buildable coverage")
        self.bag = make_product(self.store, self.fashion, "Christian Dior Bag", 9512, None, brand=self.zara,
                                options={"Color": ["Black", "Red"]}, is_best_seller=True)
        self.heels = make_product(self.store, self.fashion, "Jimmy Choo Heels", 12012, 14512,
                                  stock_status=Product.StockStatus.OUT_OF_STOCK, stock=0)
        make_product(self.store, self.fashion, "Draft thing", 100, None, status=Product.ProductStatus.DRAFT)
        make_product(make_store(name="Suspended Co", status=Store.StoreStatus.SUSPENDED), self.fashion, "Hidden", 100, None)
        refresh_category_counts()


class BrowseTests(CatalogTestCase):
    def test_categories_tree_with_counts(self):
        body = self.get("/api/v1/categories").json()
        by_slug = {c["slug"]: c for c in body}
        self.assertEqual(set(by_slug), {"beauty", "fashion"})
        self.assertEqual(by_slug["beauty"]["product_count"], 1)  # rolled up from Makeup
        self.assertEqual(by_slug["beauty"]["children"][0]["slug"], "makeup")
        self.assertEqual(by_slug["fashion"]["product_count"], 2)  # draft + suspended-store excluded

    def test_list_hides_unpublished_and_paginates(self):
        body = self.get("/api/v1/products").json()
        self.assertEqual(body["total"], 3)
        self.assertEqual((body["page"], body["per_page"], body["pages"]), (1, 12, 1))
        names = {p["name"] for p in body["items"]}
        self.assertNotIn("Draft thing", names)
        self.assertNotIn("Hidden", names)
        body = self.get("/api/v1/products", per_page=2, page=2).json()
        self.assertEqual((len(body["items"]), body["pages"]), (1, 2))

    def test_card_shape(self):
        card = next(p for p in self.get("/api/v1/products").json()["items"] if p["slug"] == self.bb.slug)
        self.assertEqual(card["category"]["slug"], "makeup")
        self.assertEqual(card["brand"], "NARS")
        self.assertEqual(card["discount_percent"], 37)
        self.assertIsNone(card["image_url"])
        self.assertIsNone(card["is_wishlisted"])  # guest

    def test_filters(self):
        def slugs(**params):
            return {p["slug"] for p in self.get("/api/v1/products", **params).json()["items"]}

        self.assertEqual(slugs(category="beauty"), {self.bb.slug})  # descendants included
        self.assertEqual(slugs(category="fashion"), {self.bag.slug, self.heels.slug})
        self.assertEqual(self.get("/api/v1/products", category="nope").status_code, 404)
        self.assertEqual(slugs(brand=["nars", "zara"]), {self.bb.slug, self.bag.slug})
        self.assertEqual(slugs(color="black"), {self.bag.slug})
        self.assertEqual(slugs(price_min=9000, price_max=13000), {self.bag.slug, self.heels.slug})
        self.assertEqual(self.get("/api/v1/products", price_min=5, price_max=1).status_code, 422)
        self.assertEqual(slugs(in_stock="true"), {self.bb.slug, self.bag.slug})
        self.assertEqual(slugs(in_stock="false"), {self.heels.slug})
        self.assertEqual(slugs(q="dior"), {self.bag.slug})
        self.assertEqual(slugs(q="nars"), {self.bb.slug})  # brand name matches too
        self.assertEqual(slugs(store=self.store.slug), {self.bb.slug, self.bag.slug, self.heels.slug})

    def test_sorting(self):
        items = self.get("/api/v1/products", sort="price_asc").json()["items"]
        self.assertEqual([p["price_cents"] for p in items], [2599, 9512, 12012])
        items = self.get("/api/v1/products", sort="price_desc").json()["items"]
        self.assertEqual([p["price_cents"] for p in items], [12012, 9512, 2599])
        self.assertEqual(self.get("/api/v1/products", sort="bogus").status_code, 422)

    def test_landing_lists(self):
        self.assertEqual([p["slug"] for p in self.get("/api/v1/products/featured").json()], [self.bb.slug])
        self.assertEqual([p["slug"] for p in self.get("/api/v1/products/best-sellers").json()], [self.bag.slug])
        top = self.get("/api/v1/products/top-discounted").json()
        self.assertEqual([p["slug"] for p in top], [self.bb.slug, self.heels.slug])

    def test_filter_options(self):
        body = self.get("/api/v1/products/filters").json()
        self.assertEqual((body["price_min_cents"], body["price_max_cents"]), (2599, 12012))
        self.assertEqual([c["value"] for c in body["colors"]], ["Black", "Red"])
        self.assertEqual({b["slug"] for b in body["brands"]}, {"nars", "zara"})

    def test_detail(self):
        res = self.get(f"/api/v1/products/{self.bb.slug}")
        self.assertEqual(res.status_code, 200, res.content)
        body = res.json()
        self.assertEqual(body["store"]["slug"], self.store.slug)
        self.assertEqual([t["name"] for t in body["option_types"]], ["Size", "Shade"])
        self.assertEqual(len(body["variants"]), 4)
        v = body["variants"][0]
        self.assertEqual((v["price_cents"], v["compare_at_price_cents"], v["in_stock"]), (2599, 4099, True))
        self.assertEqual(len(v["option_value_ids"]), 2)
        self.assertEqual(body["ratings"], {"average": 0.0, "count": 0, "distribution": {"5": 0, "4": 0, "3": 0, "2": 0, "1": 0}})
        self.assertEqual(self.get("/api/v1/products/nope").status_code, 404)
        self.assertEqual(self.get(f"/api/v1/products/{self.heels.slug}").json()["stock_status"], "out_of_stock")

    def test_related_prefers_same_category(self):
        rel = self.get(f"/api/v1/products/{self.bag.slug}/related").json()
        self.assertEqual([p["slug"] for p in rel], [self.heels.slug])


class ReviewTests(CatalogTestCase):
    URL = property(lambda self: f"/api/v1/products/{self.bb.slug}/reviews")

    def test_guest_review_requires_name_and_email_and_is_pending(self):
        res = self.client.post(self.URL, {"rating": 5, "body": "Great"})
        self.assertEqual(res.status_code, 422)
        res = self.client.post(self.URL, {"name": "Ayesha Khan", "email": "a@x.com", "rating": 5, "body": "Great"})
        self.assertEqual(res.status_code, 201, res.content)
        self.assertEqual(res.json()["status"], "pending")
        self.assertEqual(self.get(self.URL).json()["total"], 0)  # not published yet
        self.assertEqual(self.client.post(self.URL, {"name": "x", "email": "a@x.com", "rating": 6, "body": "Great"}).status_code, 422)

    def test_member_review_defaults_identity_and_once_per_product(self):
        user = make_user(full_name="Ayesha Khan")
        token = self.token_for(user)
        res = self.client.post(self.URL, {"rating": 4, "body": "Nice"}, headers={"Authorization": f"Bearer {token}"})
        self.assertEqual(res.status_code, 201, res.content)
        review = Review.objects.get()
        self.assertEqual((review.reviewer_name, review.reviewer_email, review.user_id), ("Ayesha Khan", user.email, user.id))
        res = self.client.post(self.URL, {"rating": 4, "body": "Again"}, headers={"Authorization": f"Bearer {token}"})
        self.assertEqual(res.status_code, 409)

    def test_media_validation(self):
        bad = self.client.post(self.URL, {"name": "A B", "email": "a@x.com", "rating": 5, "body": "x",
                                          "files": SimpleUploadedFile("x.exe", b"MZ", content_type="application/octet-stream")})
        self.assertEqual(bad.status_code, 422)
        fake = self.client.post(self.URL, {"name": "A B", "email": "a@x.com", "rating": 5, "body": "x",
                                           "files": SimpleUploadedFile("p.png", b"\x89PNG", content_type="image/png")})
        self.assertEqual(fake.status_code, 422)                                       # claims to be a PNG, is not one
        buf = BytesIO()
        Image.new("RGB", (4, 4), "red").save(buf, format="PNG")
        page = buf.getvalue() + b"<script>alert(1)</script>"                          # a real PNG with HTML on the end
        ok = self.client.post(self.URL, {"name": "A B", "email": "a@x.com", "rating": 5, "body": "x",
                                         "files": [SimpleUploadedFile("evil.html", page, content_type="image/png"),
                                                   SimpleUploadedFile("v.html", b"0000", content_type="video/mp4")]})
        self.assertEqual(ok.status_code, 201, ok.content)
        media = list(Review.objects.get().media.all())
        self.assertEqual([m.type for m in media], ["image", "video"])
        # The client's name never reaches the disk: the extension (which decides how it is served) is ours.
        self.assertTrue(media[0].file.name.endswith(".png") and "evil" not in media[0].file.name, media[0].file.name)
        self.assertTrue(media[1].file.name.endswith(".mp4") and ".html" not in media[1].file.name, media[1].file.name)

    def test_published_list_summary_and_sort(self):
        for name, rating in (("Ayesha Khan", 5), ("Bilal Ahmed", 3), ("Sara", 4)):
            Review.objects.create(product=self.bb, reviewer_name=name, reviewer_email="a@x.com", rating=rating, body="…", status="approved")
        Review.objects.create(product=self.bb, reviewer_name="Hidden", reviewer_email="a@x.com", rating=1, body="…")  # pending
        refresh_product_rating(self.bb)

        body = self.get(self.URL, sort="top").json()
        self.assertEqual([r["rating"] for r in body["items"]], [5, 4, 3])
        self.assertEqual(body["items"][0]["reviewer_name"], "Ayesha K.")
        self.assertEqual(body["items"][2]["reviewer_name"], "Bilal A.")
        self.assertFalse(body["items"][0]["is_verified_purchase"])

        summary = self.get(f"{self.URL}/summary").json()
        self.assertEqual((summary["average"], summary["count"]), (4.0, 3))
        self.assertEqual(summary["distribution"]["5"], 1)
        card = self.get(f"/api/v1/products/{self.bb.slug}").json()
        self.assertEqual((card["rating_avg"], card["rating_count"]), (4.0, 3))


class WishlistTests(CatalogTestCase):
    def test_wishlist_round_trip(self):
        token = self.token_for(make_user())
        self.assertEqual(self.get("/api/v1/me/wishlist").status_code, 401)
        res = self.put(f"/api/v1/me/wishlist/{self.bb.id}", token=token)
        self.assertEqual(res.status_code, 201, res.content)
        self.assertTrue(res.json()["product"]["is_wishlisted"])
        self.put(f"/api/v1/me/wishlist/{self.bb.id}", token=token)  # idempotent
        self.assertEqual(self.put("/api/v1/me/wishlist/00000000-0000-0000-0000-000000000000", token=token).status_code, 404)

        items = self.get("/api/v1/me/wishlist", token).json()
        self.assertEqual([i["product"]["slug"] for i in items], [self.bb.slug])

        # flag shows up in listings when signed in
        cards = {p["slug"]: p for p in self.get("/api/v1/products", token).json()["items"]}
        self.assertTrue(cards[self.bb.slug]["is_wishlisted"])
        self.assertFalse(cards[self.bag.slug]["is_wishlisted"])
        self.assertTrue(self.get(f"/api/v1/products/{self.bb.slug}", token).json()["is_wishlisted"])

        self.assertEqual(self.delete(f"/api/v1/me/wishlist/{self.bb.id}", token).status_code, 204)
        self.assertEqual(self.get("/api/v1/me/wishlist", token).json(), [])

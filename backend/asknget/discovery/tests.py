"""Discovery: embeddings, hybrid search, events, recommendation rails.

Runs with the offline `hash` embedder, so results here reflect lexical
similarity; the same assertions hold (more strongly) with a hosted model.
"""

from django.core.cache import cache
from django.test import override_settings

from catalog.models import Brand, Product
from core.testing import ApiTestCase, make_category, make_product, make_store, make_user
from orders.models import Order, OrderItem

from .embeddings import HashEmbedder, reset_embedder_cache
from .indexing import embed_products, product_text
from .models import ProductEmbedding, ProductEvent
from .recommendations import for_actor, frequently_bought_together, similar, trending

PRODUCTS = "/api/v1/products"


def _order(user, *products, status=Order.OrderStatus.DELIVERED) -> Order:
    order = Order.objects.create(user=user, status=status, order_number=f"T-{Order.objects.count() + 1}", contact_email=user.email,
                                 contact_first_name="T", contact_last_name="U", contact_phone="0", shipping_address={},
                                 subtotal_cents=0, total_cents=0, payment_method_type="cod")
    for p in products:
        OrderItem.objects.create(order=order, store=p.store, product=p, variant=p.variants.first(), product_name=p.name,
                                 qty=1, unit_price_cents=p.price_cents, line_total_cents=p.price_cents)
    return order


@override_settings(EMBEDDING_PROVIDER="hash", EMBEDDING_DIMENSIONS=512)
class DiscoveryBase(ApiTestCase):
    @classmethod
    def setUpTestData(cls):
        cls.store = make_store()
        cls.beauty = make_category("Beauty")
        cls.makeup = make_category("Makeup", parent=cls.beauty)
        cls.shoes = make_category("Shoes")
        nars = Brand.objects.create(name="NARS", slug="nars")
        cls.bb = make_product(cls.store, cls.makeup, "NARS BB Cream", 2599, 4099, brand=nars, options={"Shade": ["Ivory", "Natural"]},
                              short_description="Tinted moisturiser with SPF for dry skin")
        cls.cc = make_product(cls.store, cls.makeup, "Glow CC Cream", 1999, None, short_description="Colour correcting cream, light coverage")
        cls.lipstick = make_product(cls.store, cls.makeup, "Matte Lipstick", 1299, None, options={"Color": ["Red", "Nude"]},
                                    short_description="Long-wear matte lipstick")
        cls.boots = make_product(cls.store, cls.shoes, "Leather Ankle Boots", 8999, None, options={"Size": ["38", "39"]},
                                 short_description="Black leather boots with block heel")
        cls.draft = make_product(cls.store, cls.makeup, "Hidden Draft Cream", 999, None, status=Product.ProductStatus.DRAFT)

    def setUp(self):
        reset_embedder_cache()
        cache.clear()
        embed_products()


class EmbeddingTests(DiscoveryBase):
    def test_card_contains_brand_category_and_options(self):
        text = product_text(Product.objects.select_related("brand", "category").get(pk=self.bb.pk))
        for needle in ("NARS BB Cream", "Brand: NARS", "Beauty > Makeup", "Shade: Ivory, Natural", "dry skin"):
            self.assertIn(needle, text)

    def test_embed_is_idempotent_and_tracks_stale(self):
        self.assertEqual(ProductEmbedding.objects.count(), 5)
        self.assertEqual(embed_products()["embedded"], 0)

        Product.objects.filter(pk=self.cc.pk).update(name="Glow CC Cream")  # .update() bypasses signals → nothing stale
        self.cc.refresh_from_db()
        self.cc.name = "Radiant CC Cream"
        self.cc.save()
        self.assertTrue(ProductEmbedding.objects.get(pk=self.cc.pk).is_stale)
        stats = embed_products()
        self.assertEqual(stats["embedded"], 1)
        emb = ProductEmbedding.objects.get(pk=self.cc.pk)
        self.assertFalse(emb.is_stale)
        self.assertTrue(emb.text.startswith("Radiant CC Cream"))

    def test_rating_refresh_does_not_mark_stale(self):
        self.bb.rating_count = 5
        self.bb.save(update_fields=["rating_count"])
        self.assertFalse(ProductEmbedding.objects.get(pk=self.bb.pk).is_stale)

    def test_hash_embedder_is_deterministic_and_normalised(self):
        e = HashEmbedder(64)
        a, b = e.embed_query("bb cream"), e.embed_query("bb cream")
        self.assertEqual(a, b)
        self.assertAlmostEqual(sum(x * x for x in a), 1.0, places=6)
        self.assertGreater(sum(x * y for x, y in zip(a, e.embed_query("cream bb spf"))), sum(x * y for x, y in zip(a, e.embed_query("leather boots"))))


class SearchTests(DiscoveryBase):
    def test_relevance_ranking_and_result_set(self):
        res = self.get(PRODUCTS, q="bb cream")
        self.assertEqual(res.status_code, 200)
        names = [i["name"] for i in res.json()["items"]]
        self.assertEqual(names[0], "NARS BB Cream")
        self.assertIn("Glow CC Cream", names)  # shares "cream"
        self.assertNotIn("Leather Ankle Boots", names)
        self.assertNotIn("Hidden Draft Cream", names)

    def test_semantic_side_finds_description_words(self):
        names = [i["name"] for i in self.get(PRODUCTS, q="dry skin spf").json()["items"]]
        self.assertEqual(names[:1], ["NARS BB Cream"])

    def test_nonsense_query_returns_nothing(self):
        body = self.get(PRODUCTS, q="qzxjvw plorbt").json()
        self.assertEqual(body["total"], 0)
        self.assertEqual(body["items"], [])

    def test_filters_still_apply_and_explicit_sort_wins(self):
        names = [i["name"] for i in self.get(PRODUCTS, q="cream", category="shoes").json()["items"]]
        self.assertEqual(names, [])
        prices = [i["price_cents"] for i in self.get(PRODUCTS, q="cream", sort="price_asc").json()["items"]]
        self.assertEqual(prices, sorted(prices))
        self.assertEqual(len(prices), 2)

    def test_search_is_logged_once_with_result_count(self):
        self.get(PRODUCTS, q="lipstick", page=1)
        self.get(PRODUCTS, q="lipstick", page=2)
        self.get(PRODUCTS, q="zzzz nothing")
        events = list(ProductEvent.objects.filter(kind=ProductEvent.Kind.SEARCH).order_by("id").values_list("query", "result_count"))
        self.assertEqual(events, [("lipstick", 1), ("zzzz nothing", 0)])

    def test_suggest(self):
        body = self.get("/api/v1/search/suggest", q="cre").json()
        self.assertEqual({p["label"] for p in body["products"]}, {"NARS BB Cream", "Glow CC Cream"})
        self.assertEqual([b["label"] for b in self.get("/api/v1/search/suggest", q="nar").json()["brands"]], ["NARS"])
        self.assertEqual([c["slug"] for c in self.get("/api/v1/search/suggest", q="make").json()["categories"]], ["makeup"])


class EventTests(DiscoveryBase):
    def test_view_cart_wishlist_purchase_are_recorded(self):
        user = make_user()
        token = self.token_for(user)
        self.get(f"{PRODUCTS}/{self.bb.slug}", token=token)
        self.put(f"/api/v1/me/wishlist/{self.lipstick.pk}", token=token)
        self.put(f"/api/v1/me/wishlist/{self.lipstick.pk}", token=token)  # idempotent → one event
        self.post("/api/v1/cart/items", {"variant_id": str(self.cc.variants.first().pk), "qty": 1}, token=token)
        kinds = sorted(ProductEvent.objects.filter(user=user).values_list("kind", flat=True))
        self.assertEqual(kinds, ["add_to_cart", "view", "wishlist"])

    def test_guest_events_keyed_by_cart_token(self):
        res = self.client.get(f"{PRODUCTS}/{self.boots.slug}", headers={"X-Cart-Token": "guest-abc"})
        self.assertEqual(res.status_code, 200)
        ev = ProductEvent.objects.get(kind="view")
        self.assertEqual((ev.cart_token, ev.user_id, ev.product_id), ("guest-abc", None, self.boots.pk))


class RecommendationTests(DiscoveryBase):
    def test_similar_prefers_embedding_neighbours(self):
        names = [p.name for p in similar(self.bb, 3)]
        self.assertEqual(names[0], "Glow CC Cream")
        self.assertNotIn("NARS BB Cream", names)
        res = self.get(f"{PRODUCTS}/{self.bb.slug}/similar")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json()[0]["name"], "Glow CC Cream")

    def test_frequently_bought_together_uses_orders(self):
        u1, u2, u3 = make_user(), make_user(), make_user()
        _order(u1, self.bb, self.lipstick)
        _order(u2, self.bb, self.lipstick, self.boots)
        _order(u3, self.bb, self.boots, status=Order.OrderStatus.CANCELLED)  # ignored
        names = [p.name for p in frequently_bought_together(self.bb, 2)]
        self.assertEqual(names, ["Matte Lipstick", "Leather Ankle Boots"])
        res = self.get(f"{PRODUCTS}/{self.bb.slug}/frequently-bought-together")
        self.assertEqual([p["name"] for p in res.json()][:1], ["Matte Lipstick"])

    def test_frequently_bought_together_falls_back_to_similar(self):
        self.assertEqual([p.name for p in frequently_bought_together(self.bb, 1)], ["Glow CC Cream"])

    def test_trending_counts_weighted_events_then_falls_back(self):
        self.assertEqual(len(trending(3)), 3)  # no events → still fills the rail
        for _ in range(5):
            ProductEvent.objects.create(kind="view", product=self.boots, cart_token="g")
        ProductEvent.objects.create(kind="purchase", product=self.lipstick, cart_token="g")  # 6 > 5 views
        self.assertEqual([p.name for p in trending(2)], ["Matte Lipstick", "Leather Ankle Boots"])

    def test_guest_recommendations_follow_their_views(self):
        cold = self.client.get("/api/v1/recommendations").json()
        self.assertEqual(cold["strategy"], "trending")
        self.client.get(f"{PRODUCTS}/{self.bb.slug}", headers={"X-Cart-Token": "guest-1"})
        warm = self.client.get("/api/v1/recommendations", headers={"X-Cart-Token": "guest-1"}).json()
        self.assertEqual(warm["strategy"], "personalised")
        self.assertEqual([s["name"] for s in warm["seeds"]], ["NARS BB Cream"])
        self.assertEqual(warm["items"][0]["name"], "Glow CC Cream")

    def test_member_recommendations_exclude_purchases(self):
        user = make_user()
        _order(user, self.bb)  # bought BB cream → profile built from it, but not recommended again
        out = for_actor(user, "", limit=4)
        names = [p.name for p in out["items"]]
        self.assertEqual(out["strategy"], "personalised")
        self.assertNotIn("NARS BB Cream", names)
        self.assertEqual(names[0], "Glow CC Cream")
        self.assertEqual(len(names), 3)  # every other public product, topped up by trending

    def test_recommendations_endpoint_for_member(self):
        user = make_user()
        token = self.token_for(user)
        self.put(f"/api/v1/me/wishlist/{self.boots.pk}", token=token)
        body = self.get("/api/v1/recommendations", token=token, limit=2).json()
        self.assertEqual(body["strategy"], "personalised")
        self.assertEqual(body["seeds"][0]["slug"], self.boots.slug)
        self.assertEqual(len(body["items"]), 2)
        self.assertIn("is_wishlisted", body["items"][0])

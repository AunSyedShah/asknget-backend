"""Advanced Analytics (premium) — every report against a fixture with known numbers."""

from datetime import timedelta

from django.core.cache import cache
from django.utils import timezone

from catalog.models import Product
from core.testing import ApiTestCase, make_category, make_product, make_store, make_user
from discovery.models import ProductEvent
from orders.models import Order, OrderItem, OrderStatusEvent

A = "/api/v1/seller/analytics"
Fulfilment = OrderItem.FulfilmentStatus


class AnalyticsTestCase(ApiTestCase):
    """One month of history: 3 products, 4 orders (one cancelled, one returned,
    one delivered), a repeat customer, views/wishlists/carts and two searches."""

    @classmethod
    def setUpTestData(cls):
        cls.owner = make_user(is_seller=True, full_name="Sirosh Bashir")
        cls.store = make_store(owner=cls.owner, name="Huda Beauty")
        cls.store.is_premium = True
        cls.store.save(update_fields=["is_premium"])
        cls.beauty = make_category("Beauty")
        cls.bb = make_product(cls.store, cls.beauty, "BB Cream", 1000, None, stock=10)
        cls.lip = make_product(cls.store, cls.beauty, "Lip Gloss", 500, None, stock=10)
        cls.slow = make_product(cls.store, cls.beauty, "Nail File", 200, None, stock=10)
        cls.olivia = make_user(full_name="Olivia Martin")
        cls.sam = make_user(full_name="Sam Patel")
        now = timezone.now()
        cls.today = now.replace(hour=12, minute=0, second=0, microsecond=0)

        # 2 orders from Olivia (repeat), 1 from Sam, 1 guest — all this month.
        cls.o1 = cls._order(cls.olivia, [(cls.bb, 2)], Fulfilment.DELIVERED, days_ago=6)
        cls.o2 = cls._order(cls.olivia, [(cls.lip, 1)], Fulfilment.SHIPPED, days_ago=3)
        cls.o3 = cls._order(cls.sam, [(cls.bb, 1)], Fulfilment.CANCELLED, days_ago=2)
        cls.o4 = cls._order(None, [(cls.lip, 2)], Fulfilment.RETURNED, days_ago=1)

        # Events: 10 views on bb, 4 on lip, 3 on the slow mover; 2 wishlists, 3 carts.
        for product, n in ((cls.bb, 10), (cls.lip, 4), (cls.slow, 3)):
            for _ in range(n):
                ProductEvent.objects.create(kind=ProductEvent.Kind.VIEW, product=product, user=cls.olivia)
        for _ in range(2):
            ProductEvent.objects.create(kind=ProductEvent.Kind.WISHLIST, product=cls.bb, user=cls.olivia)
        for _ in range(3):
            ProductEvent.objects.create(kind=ProductEvent.Kind.ADD_TO_CART, product=cls.bb, user=cls.olivia)

    @classmethod
    def _order(cls, user, lines, status, *, days_ago=1):
        total = sum(p.price_cents * q for p, q in lines)
        first, _, last = (user.full_name if user else "Guest Buyer").partition(" ")
        order = Order.objects.create(
            user=user, status=Order.OrderStatus.CONFIRMED, order_number=f"ASK{Order.objects.count() + 1:05d}",
            country_id=cls.store.country_id, contact_first_name=first, contact_last_name=last,
            contact_email=user.email if user else "guest@example.com", contact_phone="0",
            shipping_address={}, subtotal_cents=total, total_cents=total, payment_method_type="card", payment_status="paid",
        )
        for product, qty in lines:
            OrderItem.objects.create(order=order, store=cls.store, product=product, variant=product.variants.first(),
                                     product_name=product.name, qty=qty, unit_price_cents=product.price_cents,
                                     line_total_cents=product.price_cents * qty, status=status)
        placed = timezone.now() - timedelta(days=days_ago)
        Order.objects.filter(pk=order.pk).update(placed_at=placed)
        order.refresh_from_db()
        return order

    def setUp(self):
        cache.clear()
        self.token = self.token_for(self.owner)

    def free(self):
        self.store.is_premium = False
        self.store.save(update_fields=["is_premium"])


class GatingTests(AnalyticsTestCase):
    def test_every_report_is_premium_only(self):
        self.free()
        for path in ("/overview", "/funnel", "/products", "/search", "/customers", "/fulfilment", "/export.csv"):
            res = self.get(f"{A}{path}", self.token)
            self.assertEqual(res.status_code, 403, path)
            self.assertEqual(res.json()["detail"], "premium_required")

    def test_premium_store_gets_them(self):
        for path in ("/overview", "/funnel", "/products", "/search", "/customers", "/fulfilment"):
            self.assertEqual(self.get(f"{A}{path}", self.token).status_code, 200, path)

    def test_a_seller_without_a_store_is_still_403_no_active_store(self):
        stranger = make_user(is_seller=True)
        self.assertEqual(self.get(f"{A}/overview", self.token_for(stranger)).json()["detail"], "no_active_store")


class ReportTests(AnalyticsTestCase):
    def test_overview_nets_off_cancellations_and_returns(self):
        body = self.get(f"{A}/overview", self.token).json()
        # gross = 2×1000 + 1×500 + 1×1000 (cancelled) + 2×500 (returned) = 4500
        self.assertEqual(body["gross_sales_cents"]["value"], 4500)
        self.assertEqual(body["net_sales_cents"]["value"], 2500)  # minus 1000 cancelled and 1000 returned
        self.assertEqual(body["units"]["value"], 6)
        self.assertEqual(body["orders"]["value"], 4)
        self.assertEqual(body["cancellation_rate"]["value"], 25.0)
        self.assertEqual(body["return_rate"]["value"], 25.0)
        self.assertEqual(body["repeat_order_rate"]["value"], 25.0)  # 4 orders from 3 distinct buyers
        self.assertEqual(body["aov_cents"]["value"], 625)  # net 2500 / 4 orders
        self.assertEqual(body["gross_sales_cents"]["previous"], 0)

    def test_funnel_steps_and_biggest_drop_off(self):
        body = self.get(f"{A}/funnel", self.token).json()
        steps = {s["step"]: s for s in body["steps"]}
        self.assertEqual([s["step"] for s in body["steps"]], ["views", "wishlists", "add_to_cart", "purchases"])
        self.assertEqual(steps["views"]["count"]["value"], 17)
        self.assertEqual(steps["wishlists"]["count"]["value"], 2)
        self.assertEqual(steps["add_to_cart"]["count"]["value"], 3)
        self.assertEqual(steps["purchases"]["count"]["value"], 3)  # units on non-cancelled/returned lines
        self.assertEqual(steps["views"]["from_previous_pct"], 100.0)
        self.assertEqual(steps["wishlists"]["drop_off_pct"], round((17 - 2) / 17 * 100, 2))
        self.assertEqual(body["biggest_drop_off"], "views→wishlists")
        self.assertEqual(body["view_to_purchase_pct"]["value"], round(3 / 17 * 100, 2))

    def test_products_leaderboard_and_slow_movers(self):
        body = self.get(f"{A}/products", self.token).json()
        self.assertEqual(body["products_total"], 3)
        top = {r["name"]: r for r in body["top"]}
        self.assertEqual(top["BB Cream"]["units"], 2)          # the cancelled line does not count as sold
        self.assertEqual(top["BB Cream"]["revenue_cents"], 2000)
        self.assertEqual(top["BB Cream"]["views"], 10)
        self.assertEqual(top["BB Cream"]["conversion_rate"], 10.0)  # 1 order / 10 views
        self.assertEqual([r["name"] for r in body["slow_movers"]], ["Nail File"])
        self.assertEqual(body["products_with_no_sales"], 1)
        by_views = self.get(f"{A}/products", self.token, sort="views").json()["top"]
        self.assertEqual([r["name"] for r in by_views][:1], ["BB Cream"])

    def test_search_attributes_queries_that_led_to_the_store(self):
        ProductEvent.objects.create(kind=ProductEvent.Kind.SEARCH, query="bb cream", result_count=3, user=self.sam)
        ProductEvent.objects.create(kind=ProductEvent.Kind.VIEW, product=self.bb, user=self.sam)
        ProductEvent.objects.create(kind=ProductEvent.Kind.SEARCH, query="unicorn serum", result_count=0, user=self.sam)
        body = self.get(f"{A}/search", self.token).json()
        self.assertEqual([r["query"] for r in body["store_queries"]], ["bb cream"])
        self.assertEqual(body["store_queries"][0]["product_views"], 1)
        self.assertEqual([r["query"] for r in body["market_zero_result_queries"]], ["unicorn serum"])
        self.assertFalse(body["truncated"])

    def test_search_ignores_a_view_that_is_too_late(self):
        old = timezone.now() - timedelta(hours=2)
        e = ProductEvent.objects.create(kind=ProductEvent.Kind.SEARCH, query="late", result_count=2, user=self.sam)
        ProductEvent.objects.filter(pk=e.pk).update(created_at=old)
        ProductEvent.objects.create(kind=ProductEvent.Kind.VIEW, product=self.bb, user=self.sam)
        self.assertEqual([r["query"] for r in self.get(f"{A}/search", self.token).json()["store_queries"]], [])

    def test_customers_cohorts(self):
        # Sam's only order in the fixture was cancelled, and cancelled/returned orders
        # do not make someone a customer — give him a real one so cohorts mean something.
        self._order(self.sam, [(self.lip, 1)], Fulfilment.DELIVERED, days_ago=4)
        body = self.get(f"{A}/customers", self.token).json()
        self.assertEqual(body["customers_total"], 2)       # Olivia and Sam; the guest's order was returned
        self.assertEqual(body["repeat_customers"], 1)      # Olivia ordered twice
        self.assertEqual(body["repeat_rate"], 50.0)
        self.assertEqual(body["orders_per_customer"], 1.5)
        self.assertEqual(body["spend_per_customer_cents"], 1500)  # (2500 + 500) / 2
        self.assertEqual(body["top_customers"][0]["name"], "Olivia Martin")
        self.assertEqual((body["top_customers"][0]["orders"], body["top_customers"][0]["total_spent_cents"]), (2, 2500))
        self.assertEqual(body["active_in_period"], 2)
        self.assertTrue(body["monthly"])

    def test_fulfilment_timings_and_rates(self):
        # Timings run from payment (Order.placed_at) — a per-store `pending` event is
        # never written, that state is implicit at checkout.
        placed = self.o1.placed_at
        shipped = OrderStatusEvent.objects.create(order=self.o1, store=self.store, status=Fulfilment.SHIPPED, actor="seller")
        delivered = OrderStatusEvent.objects.create(order=self.o1, store=self.store, status=Fulfilment.DELIVERED, actor="seller")
        OrderStatusEvent.objects.filter(pk=shipped.pk).update(created_at=placed + timedelta(hours=24))
        OrderStatusEvent.objects.filter(pk=delivered.pk).update(created_at=placed + timedelta(hours=72))
        body = self.get(f"{A}/fulfilment", self.token).json()
        self.assertEqual(body["orders"], 4)
        self.assertEqual(body["by_status"]["delivered"], 1)
        self.assertEqual(body["by_status"]["cancelled"], 1)
        self.assertEqual(body["to_shipped"], {"orders": 1, "median_hours": 24.0, "average_hours": 24.0})
        self.assertEqual(body["to_delivered"], {"orders": 1, "median_hours": 48.0, "average_hours": 48.0})
        self.assertEqual((body["cancellation_rate"], body["return_rate"]), (25.0, 25.0))

    def test_period_window_excludes_older_history(self):
        old = self._order(self.sam, [(self.bb, 5)], Fulfilment.DELIVERED, days_ago=400)
        self.assertEqual(self.get(f"{A}/overview", self.token, period="month").json()["units"]["value"], 6)
        self.assertGreaterEqual(self.get(f"{A}/overview", self.token, period="year").json()["units"]["value"], 6)
        self.assertEqual(Order.objects.filter(pk=old.pk).count(), 1)

    def test_csv_export(self):
        res = self.get(f"{A}/export.csv", self.token, report="products")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res["Content-Type"], "text/csv; charset=utf-8")
        body = b"".join(res.streaming_content).decode()
        self.assertIn("section,product,status", body)
        self.assertIn("slow_mover,Nail File", body)
        overview = b"".join(self.get(f"{A}/export.csv", self.token, report="overview").streaming_content).decode()
        self.assertIn("gross_sales_cents,4500", overview)

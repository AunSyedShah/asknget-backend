"""Advanced Analytics (premium) — every report against a fixture with known numbers."""

from datetime import timedelta

from django.core.cache import cache
from django.utils import timezone

from catalog.models import Product
from config.models import Setting
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

    @staticmethod
    def _ago(days: float):
        """`days` before now -- but never before the start of the current calendar month.

        "month" is a calendar period (portal.periods), so history seeded "6 days ago"
        falls outside it during the first week of every month and the whole suite
        fails on, say, the 2nd. The clock cannot be frozen instead: JWTs are minted
        with timezone.now() and validated by PyJWT against the real clock. So when
        less than a week of the month has elapsed, the same spread is scaled to fit
        in what has -- order preserved, shape unchanged."""
        now = timezone.now()
        elapsed = now - timezone.localtime(now).replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        spread = timedelta(days=7)
        if elapsed >= spread:
            return now - timedelta(days=days)
        return now - timedelta(days=days) * (elapsed / spread)

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
        placed = cls._ago(days_ago)
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

    def test_events_with_the_same_timestamp_keep_the_order_they_were_recorded_in(self):
        ProductEvent.objects.create(kind=ProductEvent.Kind.SEARCH, query="bb cream", result_count=3, user=self.sam)
        ProductEvent.objects.create(kind=ProductEvent.Kind.VIEW, product=self.bb, user=self.sam)
        ProductEvent.objects.create(kind=ProductEvent.Kind.SEARCH, query="unicorn serum", result_count=0, user=self.sam)
        ProductEvent.objects.filter(user=self.sam).update(created_at=timezone.now())     # a coarse clock: all three in one tick
        body = self.get(f"{A}/search", self.token).json()
        self.assertEqual([r["query"] for r in body["store_queries"]], ["bb cream"])      # the search before the view, not the one after

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


class StorePerformanceTests(AnalyticsTestCase):
    """The blocks the Analytics Overview screen draws beyond the four KPI cards."""

    def test_conversion_daily_series_and_category_mix(self):
        body = self.get(f"{A}/overview", self.token).json()
        # 3 store orders (one cancelled, one returned, one delivered, one shipped) over 17 views
        self.assertEqual(body["conversion_rate"]["value"], round(4 / 17 * 100, 2))
        self.assertTrue(body["daily"])
        self.assertEqual(sum(p["values"]["sales"] for p in body["daily"]), 2500.0)  # sold value, cancels excluded
        self.assertEqual([c["category"] for c in body["sales_by_category"]], ["Beauty"])
        self.assertEqual(body["sales_by_category"][0]["share_pct"], 100.0)

    def test_top_products_and_customer_snapshot(self):
        body = self.get(f"{A}/overview", self.token).json()
        top = body["top_products"]
        self.assertEqual(top[0]["rank"], 1)
        self.assertEqual(top[0]["name"], "BB Cream")
        self.assertEqual(top[0]["revenue_cents"], 2000)
        self.assertIn("image_url", top[0])
        snapshot = body["customer_snapshot"]
        self.assertEqual(sum(snapshot.values()), 1)          # only Olivia has non-cancelled orders
        self.assertEqual(snapshot["returning"], 1)

    def test_product_analytics_adds_average_revenue(self):
        body = self.get(f"{A}/products", self.token).json()
        self.assertEqual(body["top_selling_count"], 2)        # BB Cream and Lip Gloss sold
        self.assertEqual(body["avg_product_revenue_cents"], round(2500 / 3))
        self.assertIn("image_url", body["top"][0])


class SalesAnalyticsTests(AnalyticsTestCase):
    def test_gross_net_and_the_waterfall(self):
        body = self.get(f"{A}/sales", self.token).json()
        self.assertEqual(body["gross_sales_cents"]["value"], 4500)   # every paid line: 2500 that stuck + 2000 later cancelled / returned
        self.assertEqual(body["discounts_cents"]["value"], 0)
        self.assertEqual(body["refunds_cents"]["value"], 2000)       # the cancelled order and the returned one
        self.assertEqual(body["adjustments_cents"]["value"], 0)
        self.assertTrue(body["refunds_tracked"])
        self.assertEqual(body["net_sales_cents"]["value"], 2500)     # same as before: the lines that stuck
        self.assertEqual([s["label"] for s in body["revenue_breakdown"]],
                         ["Gross Sales", "Discounts", "Refunds", "Adjustments", "Net Sales"])
        self.assertEqual(body["revenue_breakdown"][-1]["amount_cents"], 2500)

    def test_discount_is_the_stores_pro_rata_share(self):
        from orders.models import Order
        from orders.services import store_discount_cents

        order = self.o1                      # 2 x 1000 from this store only
        Order.objects.filter(pk=order.pk).update(discount_cents=300)
        order.refresh_from_db()
        self.assertEqual(store_discount_cents(order, self.store.pk), 300)
        body = self.get(f"{A}/sales", self.token).json()
        self.assertEqual(body["discounts_cents"]["value"], 300)
        self.assertEqual(body["net_sales_cents"]["value"], 2200)

    def test_pro_rata_split_across_two_stores_adds_back_up(self):
        from orders.models import Order, OrderItem
        from orders.services import store_discount_cents
        from core.testing import make_store

        other = make_store(name="Other Co")
        order = self._order(self.olivia, [(self.bb, 1)], Fulfilment.DELIVERED, days_ago=5)
        OrderItem.objects.create(order=order, store=other, product=self.lip, variant=self.lip.variants.first(),
                                 product_name="Lip Gloss", qty=1, unit_price_cents=500, line_total_cents=500,
                                 status=Fulfilment.DELIVERED)
        Order.objects.filter(pk=order.pk).update(discount_cents=101)   # odd number: rounding must not lose a cent
        order.refresh_from_db()
        mine = store_discount_cents(order, self.store.pk)
        theirs = store_discount_cents(order, other.pk)
        self.assertEqual(mine + theirs, 101)
        self.assertEqual(mine, 67)   # 1000/1500 of 101, largest-remainder rounded

    def test_a_cancelled_orders_voucher_no_longer_drags_net_down(self):
        # o3 (Sam, 1000, cancelled) had a 100 voucher. Gross counts the order, the discount counts, and the
        # refund takes back only what was actually paid for it, so net does not move.
        from orders.models import Order

        Order.objects.filter(pk=self.o3.pk).update(discount_cents=100)
        body = self.get(f"{A}/sales", self.token).json()
        self.assertEqual(body["discounts_cents"]["value"], 100)
        self.assertEqual(body["refunds_cents"]["value"], 1900)       # 1000 + 1000 lines, less the 100 that was never paid
        self.assertEqual(body["net_sales_cents"]["value"], 2500)

    def test_a_fully_refunded_order_nets_to_exactly_zero(self):
        from orders.models import Order

        Order.objects.filter(pk=self.o4.pk).update(discount_cents=250)       # the guest's returned order: 1000 of lines
        body = self.get(f"{A}/sales", self.token).json()
        self.assertEqual(body["net_sales_cents"]["value"], 2500)

    def test_an_unpaid_order_is_not_a_sale(self):
        from orders.models import Order

        order = self._order(self.sam, [(self.bb, 3)], Fulfilment.PENDING, days_ago=1)
        Order.objects.filter(pk=order.pk).update(payment_status="unpaid", status="pending_payment")
        self.assertEqual(self.get(f"{A}/sales", self.token).json()["gross_sales_cents"]["value"], 4500)

    def test_staff_adjustments_come_from_the_ledger_and_are_signed(self):
        from ledger import services as ledger

        staff = make_user(is_staff=True)
        ledger.adjust(self.store, amount_cents=1500, note="Goodwill credit", by=staff)
        ledger.adjust(self.store, amount_cents=-400, note="Chargeback", by=staff)
        body = self.get(f"{A}/sales", self.token).json()
        self.assertEqual(body["adjustments_cents"]["value"], 1100)
        self.assertEqual(body["net_sales_cents"]["value"], 2500 + 1100)                    # a credit adds to net
        legs = {s["label"]: s["amount_cents"] for s in body["revenue_breakdown"]}
        self.assertEqual((legs["Adjustments"], legs["Net Sales"]), (1100, 3600))
        # the legs add up: gross - discounts - refunds + adjustments = net
        self.assertEqual(legs["Gross Sales"] + legs["Discounts"] + legs["Refunds"] + legs["Adjustments"], legs["Net Sales"])

    def test_the_chart_adds_up_to_the_cards(self):
        from ledger import services as ledger

        ledger.adjust(self.store, amount_cents=700, note="Goodwill credit", by=make_user(is_staff=True))
        body = self.get(f"{A}/sales", self.token).json()
        gross = sum(p["values"]["gross"] for p in body["revenue_over_time"])
        net = sum(p["values"]["net"] for p in body["revenue_over_time"])
        self.assertEqual((gross, net), (body["gross_sales_cents"]["value"], body["net_sales_cents"]["value"]))

    def test_channel_mix_defaults_to_website(self):
        body = self.get(f"{A}/sales", self.token).json()
        self.assertEqual([c["channel"] for c in body["sales_by_channel"]], ["web"])
        self.assertEqual(body["sales_by_channel"][0]["share_pct"], 100.0)

    def test_buckets_and_csv(self):
        self.assertEqual(self.get(f"{A}/sales", self.token, bucket="monthly").json()["bucket"], "monthly")
        rows = b"".join(self.get(f"{A}/export.csv", self.token, report="sales").streaming_content).decode()
        self.assertIn("gross_sales_cents,4500", rows)
        self.assertIn("refunds_cents,2000", rows)
        self.assertIn("breakdown_Net Sales", rows)
        self.assertIn("channel_web", rows)


class ProductPerformanceTests(AnalyticsTestCase):
    """The Product Analytics screen: a daily chart, the best and worst converters, judged on a threshold the admin sets.

    Fixture views: BB Cream 10, Lip Gloss 4, Nail File 3. Orders that count: BB Cream 1 (10 % conversion),
    Lip Gloss 1 (25 %), Nail File 0.
    """

    def products(self, **params):
        res = self.get(f"{A}/products", self.token, **params)
        self.assertEqual(res.status_code, 200, res.content)
        return res.json()

    def names(self, rows):
        return [r["name"] for r in rows]

    def test_the_default_threshold_is_twenty_views_so_a_young_store_judges_nobody(self):
        body = self.products()
        self.assertEqual((body["low_performer_min_views"], body["low_performers"], body["conversion_leaders"]), (20, [], []))

    def test_the_admin_sets_how_many_views_count_as_enough(self):
        Setting.objects.create(key="analytics.low_performer_min_views", value=4)
        body = self.products()
        self.assertEqual(body["low_performer_min_views"], 4)
        self.assertEqual(self.names(body["low_performers"]), ["BB Cream", "Lip Gloss"])          # 10 % before 25 %
        self.assertEqual(self.names(body["conversion_leaders"]), ["Lip Gloss", "BB Cream"])
        self.assertNotIn("Nail File", self.names(body["low_performers"]))                         # 3 views: too little data to judge

    def test_a_store_can_have_its_own_threshold(self):
        Setting.objects.create(key="analytics.low_performer_min_views", value=4)
        Setting.objects.create(key="analytics.low_performer_min_views", scope="store", store=self.store, value=10)
        self.assertEqual(self.names(self.products()["low_performers"]), ["BB Cream"])

    def test_the_admin_sets_the_length_of_the_list(self):
        Setting.objects.create(key="analytics.low_performer_min_views", value=3)
        Setting.objects.create(key="analytics.low_performer_limit", value=1)
        body = self.products()
        self.assertEqual(self.names(body["low_performers"]), ["Nail File"])                       # 0 % is the worst
        self.assertEqual(self.names(body["conversion_leaders"]), ["Lip Gloss"])

    def test_only_live_products_are_judged(self):
        Setting.objects.create(key="analytics.low_performer_min_views", value=3)
        Product.objects.filter(name="Nail File").update(status="archived")
        Product.objects.filter(name="Lip Gloss").update(status="draft")
        self.assertEqual(self.names(self.products()["low_performers"]), ["BB Cream"])

    def test_cart_adds_are_counted_per_product(self):
        rows = {r["name"]: r for r in self.products()["top"]}
        self.assertEqual((rows["BB Cream"]["add_to_cart"], rows["Lip Gloss"]["add_to_cart"]), (3, 0))

    def test_the_daily_chart_has_a_point_for_every_day_up_to_today_and_adds_up(self):
        body = self.products()
        daily = body["daily"]
        today = timezone.localdate()
        self.assertEqual(len(daily), today.day)                                                    # the 1st through today, quiet days included
        self.assertEqual([d["date"] for d in daily], sorted(d["date"] for d in daily))
        self.assertEqual(daily[-1]["date"], today.isoformat())
        self.assertEqual(sum(d["revenue_cents"] for d in daily), sum(r["revenue_cents"] for r in body["top"]))
        self.assertEqual(sum(d["units"] for d in daily), sum(r["units"] for r in body["top"]))
        self.assertEqual(sum(d["views"] for d in daily), 17)                                       # 10 + 4 + 3, all recorded today
        self.assertEqual(daily[-1]["views"], 17)
        self.assertGreater(sum(d["orders"] for d in daily), 0)

    def test_the_chart_follows_the_period(self):
        week = self.products(period="week")["daily"]
        self.assertLessEqual(len(week), 7)
        self.assertEqual(week[-1]["date"], timezone.localdate().isoformat())

    def test_the_csv_export_includes_the_new_lists(self):
        Setting.objects.create(key="analytics.low_performer_min_views", value=4)
        res = self.get(f"{A}/export.csv", self.token, report="products")
        self.assertEqual(res.status_code, 200)
        self.assertIn("low_performer", b"".join(res.streaming_content).decode())

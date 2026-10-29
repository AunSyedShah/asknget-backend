"""Query budgets for the hot endpoints.

Each test pins the number of SQL queries a request may issue. The numbers are
not sacred — if you add a feature that legitimately needs one more query, bump
the budget in the same commit. What must never happen silently is a budget that
scales with the number of rows (that is what django-zeal catches) or a slow
creep nobody noticed (that is what these pins catch).

Fixture sizes are deliberately > 1 so per-row queries would show up.

Every request pays one query up front: `markets.middleware.RegionMiddleware`
loads the country (with its policies) that the request resolves to.
"""

from datetime import timedelta

from django.core.cache import cache
from django.utils import timezone

from catalog.models import Brand, Review
from catalog.services import refresh_stock_status
from core.testing import ApiTestCase, make_category, make_product, make_store, make_user, make_staff
from discovery.indexing import embed_products
from orders.models import Order, OrderItem
from returns.models import ReturnEvent, ReturnItem, ReturnPhoto, ReturnReason, ReturnRequest, ReturnShipment

PRODUCTS = 6
ORDERS = 5


class QueryBudgetTests(ApiTestCase):
    @classmethod
    def setUpTestData(cls):
        cls.owner = make_user(is_seller=True, full_name="Sirosh Bashir")
        cls.store = make_store(owner=cls.owner)
        beauty = make_category("Beauty")
        cls.makeup = make_category("Makeup", parent=beauty)
        brand = Brand.objects.create(name="NARS", slug="nars")
        cls.products = [
            make_product(cls.store, cls.makeup, f"Product {i}", 1000 + i * 100, 2000 + i * 100, brand=brand,
                         options={"Shade": ["Ivory", "Natural"], "Size": ["15 ml", "30 ml"]}, stock=5,
                         short_description="cream for dry skin")
            for i in range(PRODUCTS)
        ]
        for p in cls.products:
            refresh_stock_status(p)
            for n in range(3):
                Review.objects.create(product=p, reviewer_name=f"R {n}", reviewer_email="r@example.com", rating=4 + n % 2, body="ok", status="approved")
        cls.store.is_premium = True  # the premium reports are part of the hot path for these stores
        cls.store.save(update_fields=["is_premium"])
        cls.buyer = make_user(full_name="Olivia Martin")
        now = timezone.now()
        for i in range(ORDERS):
            order = Order.objects.create(
                user=cls.buyer, status=Order.OrderStatus.CONFIRMED, order_number=f"ASK{i:05d}", country_id=cls.store.country_id, contact_first_name="Olivia", contact_last_name="Martin",
                contact_email=cls.buyer.email, contact_phone="0", shipping_address={"city": "Cape Town"}, subtotal_cents=1000, total_cents=1700,
                shipping_cents=700, payment_method_type="card", payment_status="paid",
            )
            for p in cls.products[i % 3 : i % 3 + 2]:
                OrderItem.objects.create(order=order, store=cls.store, product=p, variant=p.variants.first(), product_name=p.name,
                                         qty=1, unit_price_cents=p.price_cents, line_total_cents=p.price_cents)
            Order.objects.filter(pk=order.pk).update(placed_at=now - timedelta(days=i * 9))
        embed_products()

    def setUp(self):
        cache.clear()  # pins are cold-cache numbers (customers rollup, FBT), independent of test order
        self.seller = self.token_for(self.owner)
        self.member = self.token_for(self.buyer)

    def budget(self, n, url, token=None, **params):
        with self.assertNumQueries(n):
            res = self.get(url, token=token, **params)
        self.assertEqual(res.status_code, 200, res.content)
        return res.json()

    # Storefront -----------------------------------------------------------

    def test_product_list(self):
        body = self.budget(4, "/api/v1/products", per_page=12)
        self.assertEqual(body["total"], PRODUCTS)

    def test_product_list_signed_in_adds_wishlist_lookup_only(self):
        self.budget(6, "/api/v1/products", token=self.member, per_page=12)  # +auth user, +wishlist ids (once)

    def test_product_search(self):
        body = self.budget(9, "/api/v1/products", q="cream")
        self.assertEqual(body["total"], PRODUCTS)

    def test_product_detail(self):
        self.budget(10, f"/api/v1/products/{self.products[0].slug}")

    def test_product_reviews(self):
        self.budget(6, f"/api/v1/products/{self.products[0].slug}/reviews")

    def test_recommendations_guest_and_member(self):
        self.budget(4, "/api/v1/recommendations")
        self.budget(15, "/api/v1/recommendations", token=self.member)  # profile + kNN + trending top-up + seeds; constant in rows

    def test_my_orders(self):
        body = self.budget(6, "/api/v1/me/orders", token=self.member)
        self.assertEqual(len(body["items"]), ORDERS)

    # Seller portal --------------------------------------------------------

    def test_seller_products(self):
        body = self.budget(5, "/api/v1/seller/products", token=self.seller)
        self.assertEqual(body["total"], PRODUCTS)

    def test_seller_inventory(self):
        self.budget(8, "/api/v1/seller/inventory", token=self.seller)

    def test_seller_orders_list_and_detail(self):
        body = self.budget(4, "/api/v1/seller/orders", token=self.seller)
        self.assertEqual(body["total"], ORDERS)
        self.budget(7, f"/api/v1/seller/orders/{body['items'][0]['id']}", token=self.seller)

    def test_seller_customers(self):
        self.budget(5, "/api/v1/seller/customers", token=self.seller)

    def test_seller_dashboard(self):
        self.budget(22, "/api/v1/seller/dashboard", token=self.seller)

    # Premium (sprint 5) --------------------------------------------------

    def test_seller_chat_inbox(self):
        self.budget(4, "/api/v1/seller/chat", token=self.seller)  # plan gate is a column on the store, not a query

    def test_seller_analytics_overview(self):
        # Store Performance is five blocks in one call: KPI windows, conversion, the
        # daily series, category mix, top products (+images) and the customer rollup.
        self.budget(15, "/api/v1/seller/analytics/overview", token=self.seller)

    def test_seller_analytics_sales(self):
        self.budget(9, "/api/v1/seller/analytics/sales", token=self.seller)   # was 10: refunds now come from the same pass as gross

    # Returns --------------------------------------------------------------

    def _returns(self):
        """One return per order, each with two lines, a photo, a few events and a shipment: more than one of everything."""
        from django.core.files.uploadedfile import SimpleUploadedFile

        reason = ReturnReason.objects.get(code="changed_mind")
        for i, order in enumerate(Order.objects.filter(user=self.buyer).order_by("order_number")):
            order.items.update(status="delivered")
            req = ReturnRequest.objects.create(
                reference=f"RET-{i:05d}-1", order=order, store=self.store, user=self.buyer, country_id=self.store.country_id, reason=reason, reason_kind=reason.kind,
                currency="ZAR", items_cents=2000, refund_cents=2000, response_due_at=timezone.now() + timedelta(days=3),
            )
            for item in order.items.all():
                ReturnItem.objects.create(request=req, order_item=item, qty=1, line_total_cents=item.line_total_cents)
            ReturnPhoto.objects.create(request=req, image=SimpleUploadedFile("p.png", b"x", content_type="image/png"))
            for status in ("requested", "approved"):
                ReturnEvent.objects.create(request=req, status=status, actor="buyer")
            ReturnShipment.objects.create(request=req, courier="manual")
        return ReturnRequest.objects.order_by("reference").first()

    def test_my_returns_list_and_detail(self):
        first = self._returns()
        body = self.budget(4, "/api/v1/me/returns", token=self.member)
        self.assertEqual(body["total"], ORDERS)
        self.budget(8, f"/api/v1/me/returns/{first.pk}", token=self.member)

    def test_seller_returns_list_detail_and_tabs(self):
        first = self._returns()
        body = self.budget(5, "/api/v1/seller/returns", token=self.seller)
        self.assertEqual(body["total"], ORDERS)
        self.budget(9, f"/api/v1/seller/returns/{first.pk}", token=self.seller)
        self.budget(5, "/api/v1/seller/returns/summary", token=self.seller)

    def test_admin_returns_queue(self):
        self._returns()
        staff = self.token_for(make_staff())
        body = self.budget(6, "/api/v1/admin/returns", token=staff)      # +2 since sprint 9: the staff member's permissions and markets
        self.assertEqual(body["total"], ORDERS)

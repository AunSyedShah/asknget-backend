"""Seller portal: products wizard + review, inventory, orders, customers, dashboard."""

import io
from datetime import timedelta

from django.core.files.uploadedfile import SimpleUploadedFile
from PIL import Image
from django.utils import timezone

from catalog.models import Brand, Category, Product, ProductVariant, StockMovement
from core.testing import ApiTestCase, make_category, make_product, make_store, make_user, make_staff
from orders.models import Order, OrderItem, OrderStatusEvent
from sellers.models import Store

P = "/api/v1/seller/products"


def png(name="a.png"):
    buf = io.BytesIO()
    Image.new("RGB", (4, 4), "red").save(buf, format="PNG")
    return SimpleUploadedFile(name, buf.getvalue(), content_type="image/png")


class PortalBase(ApiTestCase):
    @classmethod
    def setUpTestData(cls):
        cls.owner = make_user(is_seller=True, full_name="Sirosh Bashir")
        cls.store = make_store(owner=cls.owner, name="Huda Beauty")
        cls.other_store = make_store(name="Other Shop")
        cls.beauty = make_category("Beauty")
        cls.makeup = make_category("Makeup", parent=cls.beauty)
        cls.brand = Brand.objects.create(name="NARS", slug="nars")
        cls.staff = make_staff()

    def setUp(self):
        self.token = self.token_for(self.owner)

    def _order(self, *lines, user=None, store=None, status="pending", order_status=Order.OrderStatus.CONFIRMED,
               email=None, name=("Olivia", "Martin"), payment="card", placed_at=None) -> Order:
        """lines: (product, qty). Creates a paid buyer order with this store's items."""
        store = store or self.store
        user = user if user is not None else None
        order = Order.objects.create(
            user=user, status=order_status, order_number=f"ASK{Order.objects.count() + 1:05d}", country_id=store.country_id,
            contact_first_name=name[0], contact_last_name=name[1], contact_email=email or (user.email if user else "guest@example.com"),
            contact_phone="+27 82 123 4567", shipping_address={"line1": "123 Main Street", "city": "Cape Town", "postal_code": "6001", "country": "ZA"},
            subtotal_cents=sum(p.price_cents * q for p, q in lines), total_cents=sum(p.price_cents * q for p, q in lines) + 700,
            shipping_cents=700, payment_method_type=payment, payment_status="paid" if order_status != "pending_payment" else "unpaid",
        )
        for p, q in lines:
            OrderItem.objects.create(order=order, store=p.store, product=p, variant=p.variants.first(), product_name=p.name,
                                     qty=q, unit_price_cents=p.price_cents, line_total_cents=p.price_cents * q, status=status)
        if placed_at:
            Order.objects.filter(pk=order.pk).update(placed_at=placed_at)
            order.refresh_from_db()
        return order


class AuthTests(PortalBase):
    def test_requires_active_store(self):
        applicant = make_user(is_seller=True)
        res = self.get(P, token=self.token_for(applicant))
        self.assertEqual(res.status_code, 403)
        self.assertEqual(res.json()["detail"], "no_active_store")
        buyer = make_user()
        self.assertEqual(self.get(P, token=self.token_for(buyer)).status_code, 403)
        self.assertEqual(self.get(P).status_code, 401)


class ProductWizardTests(PortalBase):
    def test_full_wizard_to_live(self):
        # Step 1
        res = self.post(P, {"name": "NARS BB Cream", "category_id": str(self.makeup.pk), "brand_id": str(self.brand.pk),
                            "tags": ["bb cream", " BB Cream ", "spf"], "short_description": "Tinted moisturiser"}, token=self.token)
        self.assertEqual(res.status_code, 201, res.content)
        body = res.json()
        pid = body["id"]
        self.assertEqual(body["status"], "draft")
        self.assertEqual(body["tags"], ["bb cream", "spf"])
        self.assertEqual(body["category_path"], ["Beauty", "Makeup"])
        self.assertEqual(len(body["variants"]), 1)  # default variant
        self.assertEqual(body["completion"]["missing"], ["image", "price", "stock"])

        # Step 2
        res = self.client.post(f"{P}/{pid}/images", {"file": png(), "alt": "front"}, headers={"Authorization": f"Bearer {self.token}"})
        self.assertEqual(res.status_code, 201, res.content)
        self.assertTrue(res.json()["is_primary"])
        res = self.client.post(f"{P}/{pid}/images", {"file": png("b.png")}, headers={"Authorization": f"Bearer {self.token}"})
        img2 = res.json()["id"]
        img1 = self.get(f"{P}/{pid}", token=self.token).json()["images"][0]["id"]
        res = self.put(f"{P}/{pid}/images/order", {"image_ids": [img2, img1]}, token=self.token)
        self.assertEqual([i["is_primary"] for i in res.json()["images"]], [True, False])

        # Step 3
        res = self.patch(f"{P}/{pid}", {"price_cents": 2599, "compare_at_price_cents": 4099, "low_stock_threshold": 3}, token=self.token)
        self.assertEqual(res.status_code, 200, res.content)
        res = self.put(f"{P}/{pid}/options", {"options": [{"name": "Shade", "values": [{"value": "Ivory"}, {"value": "Natural", "swatch_hex": "#EEDDCC"}]},
                                                          {"name": "Size", "values": [{"value": "15 ml"}, {"value": "30 ml"}]}]}, token=self.token)
        self.assertEqual(res.status_code, 200, res.content)
        variants = res.json()["variants"]
        self.assertEqual(len(variants), 4)
        self.assertEqual(variants[0]["option_values"], {"Shade": "Ivory", "Size": "15 ml"})
        self.assertEqual(res.json()["completion"]["missing"], ["stock"])
        for v in variants:
            r = self.patch(f"{P}/{pid}/variants/{v['id']}", {"stock_qty": 5, "sku": f"BB-{v['option_values']['Shade'][:3]}-{v['option_values']['Size'][:2]}"}, token=self.token)
            self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(StockMovement.objects.filter(product_id=pid, reason="manual").count(), 4)
        body = self.get(f"{P}/{pid}", token=self.token).json()
        self.assertEqual((body["stock_qty"], body["stock_status"]), (20, "in_stock"))

        # Step 4
        self.patch(f"{P}/{pid}", {"weight_grams": 120, "ships_from": "Cape Town"}, token=self.token)
        # Step 5
        res = self.post(f"{P}/{pid}/status", {"action": "submit"}, token=self.token)
        self.assertEqual(res.status_code, 200, res.content)
        self.assertEqual(res.json()["status"], "pending")
        self.assertEqual(res.json()["completion"]["ready_to_submit"], True)
        self.assertEqual(self.patch(f"{P}/{pid}", {"name": "x"}, token=self.token).status_code, 409)  # locked while under review

        # Not public yet, then staff approves
        self.assertEqual(self.get(f"/api/v1/products/{body['slug']}").status_code, 404)
        staff = self.token_for(self.staff)
        queue = self.get("/api/v1/admin/products", token=staff).json()
        self.assertEqual([r["name"] for r in queue["items"]], ["NARS BB Cream"])
        self.assertEqual(self.post(f"/api/v1/admin/products/{pid}/approve", token=staff).status_code, 200)
        self.assertEqual(self.get(f"/api/v1/products/{body['slug']}").status_code, 200)
        body = self.get(f"{P}/{pid}", token=self.token).json()
        self.assertEqual(body["status"], "active")
        self.assertTrue(body["public_url"].endswith(f"/products/{body['slug']}"))
        # searchable by tag once embedded
        from discovery.indexing import embed_products

        embed_products()
        self.assertEqual(self.get("/api/v1/products", q="spf").json()["total"], 1)

    def test_submit_requires_completion_and_reject_round_trip(self):
        pid = self.post(P, {"name": "Thing", "category_id": str(self.makeup.pk)}, token=self.token).json()["id"]
        res = self.post(f"{P}/{pid}/status", {"action": "submit"}, token=self.token)
        self.assertEqual(res.status_code, 422)
        self.assertIn("image", res.json()["detail"])
        self.client.post(f"{P}/{pid}/images", {"file": png()}, headers={"Authorization": f"Bearer {self.token}"})
        self.patch(f"{P}/{pid}", {"price_cents": 1000}, token=self.token)
        vid = self.get(f"{P}/{pid}", token=self.token).json()["variants"][0]["id"]
        self.patch(f"{P}/{pid}/variants/{vid}", {"stock_qty": 2}, token=self.token)
        self.assertEqual(self.post(f"{P}/{pid}/status", {"action": "submit"}, token=self.token).json()["status"], "pending")
        staff = self.token_for(self.staff)
        self.assertEqual(self.post(f"/api/v1/admin/products/{pid}/reject", {"reason": "Blurry photo"}, token=staff).status_code, 200)
        body = self.get(f"{P}/{pid}", token=self.token).json()
        self.assertEqual((body["status"], body["rejection_reason"]), ("rejected", "Blurry photo"))
        self.assertEqual(self.get(f"{P}/kpis", token=self.token).json()["tabs"]["rejected"], 1)
        # fix and resubmit
        self.assertEqual(self.post(f"{P}/{pid}/status", {"action": "submit"}, token=self.token).json()["status"], "pending")

    def test_validation(self):
        pid = self.post(P, {"name": "Thing", "category_id": str(self.makeup.pk)}, token=self.token).json()["id"]
        self.assertEqual(self.patch(f"{P}/{pid}", {"price_cents": 1000, "compare_at_price_cents": 900}, token=self.token).status_code, 422)
        self.assertEqual(self.post(P, {"name": "x", "category_id": str(self.makeup.pk), "brand_id": str(self.store.pk)}, token=self.token).status_code, 422)
        self.assertEqual(self.put(f"{P}/{pid}/options", {"options": [{"name": "Size", "values": [{"value": "S"}, {"value": "s"}]}]}, token=self.token).status_code, 422)
        # can't touch another store's product
        foreign = make_product(self.other_store, self.makeup, "Foreign")
        self.assertEqual(self.get(f"{P}/{foreign.pk}", token=self.token).status_code, 404)

    def test_options_keep_matching_variants(self):
        pid = self.post(P, {"name": "Lipstick", "category_id": str(self.makeup.pk)}, token=self.token).json()["id"]
        body = self.put(f"{P}/{pid}/options", {"options": [{"name": "Color", "values": [{"value": "Red"}, {"value": "Nude"}]}]}, token=self.token).json()
        red = next(v for v in body["variants"] if v["option_values"]["Color"] == "Red")
        self.patch(f"{P}/{pid}/variants/{red['id']}", {"stock_qty": 7, "sku": "LIP-RED"}, token=self.token)
        body = self.put(f"{P}/{pid}/options", {"options": [{"name": "Color", "values": [{"value": "Red"}, {"value": "Pink"}]}]}, token=self.token).json()
        by_color = {v["option_values"]["Color"]: v for v in body["variants"]}
        self.assertEqual(set(by_color), {"Red", "Pink"})
        self.assertEqual((by_color["Red"]["sku"], by_color["Red"]["stock_qty"]), ("LIP-RED", 7))
        self.assertEqual(by_color["Pink"]["stock_qty"], 0)
        self.assertEqual(ProductVariant.objects.filter(product_id=pid).count(), 2)  # Nude deleted (no orders)

    def test_actions_archive_unpublish_duplicate_delete(self):
        product = make_product(self.store, self.makeup, "Live One", brand=self.brand, options={"Size": ["S", "M"]})
        self.assertEqual(self.delete(f"{P}/{product.pk}", token=self.token).status_code, 409)  # active → archive first
        self.assertEqual(self.post(f"{P}/{product.pk}/status", {"action": "unpublish"}, token=self.token).json()["status"], "draft")
        self.assertEqual(self.post(f"{P}/{product.pk}/status", {"action": "archive"}, token=self.token).json()["status"], "archived")
        self.assertEqual(self.post(f"{P}/{product.pk}/status", {"action": "unarchive"}, token=self.token).json()["status"], "active")
        res = self.post(f"{P}/{product.pk}/duplicate", token=self.token)
        self.assertEqual(res.status_code, 201)
        copy = res.json()
        self.assertEqual((copy["name"], copy["status"], len(copy["variants"]), copy["stock_qty"]), ("Live One (copy)", "draft", 2, 0))
        self.assertEqual(self.delete(f"{P}/{copy['id']}", token=self.token).status_code, 204)
        self.assertEqual(self.get(f"{P}/{copy['id']}", token=self.token).status_code, 404)
        self.assertTrue(Product.all_objects.get(pk=copy["id"]).deleted_at)


class ProductListTests(PortalBase):
    def test_rows_tabs_filters_and_sales_columns(self):
        bb = make_product(self.store, self.makeup, "BB Cream", 2599, 4099, brand=self.brand, stock=2)
        lip = make_product(self.store, self.makeup, "Lipstick", 1299, None, stock=0)
        make_product(self.store, self.makeup, "Draft Thing", status=Product.ProductStatus.DRAFT)
        make_product(self.other_store, self.makeup, "Not mine")
        from catalog.services import refresh_stock_status

        for p in (bb, lip):
            refresh_stock_status(p)
        buyer = make_user()
        self._order((bb, 2), user=buyer)
        self._order((bb, 1), user=buyer, status="cancelled")            # excluded
        self._order((bb, 3), user=buyer, order_status="pending_payment")  # unpaid, excluded

        body = self.get(P, token=self.token).json()
        self.assertEqual(body["total"], 3)
        rows = {r["name"]: r for r in body["items"]}
        self.assertEqual((rows["BB Cream"]["sold_qty"], rows["BB Cream"]["earned_cents"], rows["BB Cream"]["stock_qty"]), (2, 5198, 2))
        self.assertEqual(rows["BB Cream"]["stock_status"], "low_stock")
        self.assertEqual(rows["Lipstick"]["stock_status"], "out_of_stock")
        self.assertEqual(self.get(P, token=self.token, status="draft").json()["total"], 1)
        self.assertEqual(self.get(P, token=self.token, stock="out").json()["items"][0]["name"], "Lipstick")
        self.assertEqual(self.get(P, token=self.token, q="lip").json()["total"], 1)
        self.assertEqual([r["name"] for r in self.get(P, token=self.token, sort="sold").json()["items"]][0], "BB Cream")
        kpis = self.get(f"{P}/kpis", token=self.token).json()
        self.assertEqual(kpis["tabs"], {"all": 3, "active": 2, "pending": 0, "draft": 1, "rejected": 0, "archived": 0})
        self.assertEqual(kpis["sold_qty"]["value"], 2)
        self.assertEqual(kpis["new_added"]["value"], 3)

    def test_analytics(self):
        bb = make_product(self.store, self.makeup, "BB Cream", 2599, 4099)
        self.client.get(f"/api/v1/products/{bb.slug}", headers={"X-Cart-Token": "g1"})
        self.client.get(f"/api/v1/products/{bb.slug}", headers={"X-Cart-Token": "g2"})
        self._order((bb, 2), user=make_user())
        body = self.get(f"{P}/{bb.pk}/analytics", token=self.token).json()
        self.assertEqual((body["views"]["value"], body["sold_qty"]["value"], body["earned_cents"]["value"]), (2, 2, 5198))
        self.assertEqual(body["conversion_rate"]["value"], 50.0)
        self.assertEqual(len(body["daily"]), 30)


class InventoryTests(PortalBase):
    def test_rows_kpis_adjust_and_movements(self):
        from catalog.services import refresh_stock_status

        bb = make_product(self.store, self.makeup, "BB Cream", options={"Shade": ["Ivory", "Natural"]}, stock=2)
        boots = make_product(self.store, self.makeup, "Boots", stock=0)
        make_product(self.other_store, self.makeup, "Not mine")
        for p in (bb, boots):
            refresh_stock_status(p)
        self._order((bb, 1), user=make_user())  # pending → reserved

        body = self.get("/api/v1/seller/inventory", token=self.token).json()
        self.assertEqual(body["total"], 2)
        rows = {r["name"]: r for r in body["items"]}
        self.assertEqual((rows["BB Cream"]["on_hand"], rows["BB Cream"]["reserved"], rows["BB Cream"]["variant_count"]), (4, 1, 2))
        self.assertEqual(rows["Boots"]["stock_status"], "out_of_stock")
        kpis = self.get("/api/v1/seller/inventory/kpis", token=self.token).json()
        self.assertEqual((kpis["total_products"], kpis["total_variants"], kpis["total_units"], kpis["out_of_stock"], kpis["low_stock"]), (2, 3, 4, 1, 1))
        self.assertEqual(kpis["tabs"]["recent"], 0)

        vid = rows["Boots"]["variants"][0]["id"]
        res = self.post(f"/api/v1/seller/inventory/{vid}/adjust", {"delta": 50, "note": "Received"}, token=self.token)
        self.assertEqual(res.status_code, 200, res.content)
        self.assertEqual(res.json()["stock_qty"], 50)
        self.assertEqual(self.post(f"/api/v1/seller/inventory/{vid}/adjust", {"set": 50}, token=self.token).status_code, 422)
        self.assertEqual(self.post(f"/api/v1/seller/inventory/{vid}/adjust", {"delta": -60}, token=self.token).status_code, 409)
        self.assertEqual(self.post(f"/api/v1/seller/inventory/{vid}/adjust", {"set": 5, "delta": 1}, token=self.token).status_code, 422)
        res = self.post(f"/api/v1/seller/inventory/{vid}/adjust", {"set": 3}, token=self.token)
        self.assertEqual(res.json()["stock_qty"], 3)
        boots.refresh_from_db()
        self.assertEqual(boots.stock_status, "low_stock")
        moves = self.get(f"/api/v1/seller/inventory/{vid}/movements", token=self.token).json()
        self.assertEqual([(m["delta"], m["qty_after"], m["reason"]) for m in moves], [(-47, 3, "manual"), (50, 50, "manual")])
        self.assertEqual(moves[0]["actor"], "Sirosh Bashir")
        self.assertEqual(self.get("/api/v1/seller/inventory", token=self.token, tab="recent").json()["total"], 1)
        # another store's variant is invisible
        foreign = ProductVariant.objects.filter(product__store=self.other_store).first()
        self.assertEqual(self.post(f"/api/v1/seller/inventory/{foreign.pk}/adjust", {"delta": 1}, token=self.token).status_code, 404)

    def test_export_csv(self):
        make_product(self.store, self.makeup, "BB Cream", options={"Shade": ["Ivory", "Natural"]}, stock=2)
        res = self.get("/api/v1/seller/inventory/export.csv", token=self.token)
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res["Content-Type"], "text/csv; charset=utf-8")
        lines = b"".join(res.streaming_content).decode().strip().splitlines()
        self.assertEqual(len(lines), 3)
        self.assertTrue(lines[0].startswith("product,sku,options"))
        self.assertIn("Shade: Ivory", lines[1])


class OrderTests(PortalBase):
    O = "/api/v1/seller/orders"

    def setUp(self):
        super().setUp()
        self.bb = make_product(self.store, self.makeup, "BB Cream", 4999, None, stock=10)
        self.teddy = make_product(self.store, self.makeup, "Teddy Bear", 2299, None, stock=10)
        self.foreign = make_product(self.other_store, self.makeup, "Foreign Lamp", 1000, None, stock=10)
        self.olivia = make_user(full_name="Olivia Martin")

    def test_list_only_shows_my_paid_slice(self):
        mixed = self._order((self.bb, 1), (self.teddy, 2), (self.foreign, 1), user=self.olivia)  # multi-store
        self._order((self.foreign, 1), user=self.olivia)                                       # not mine
        self._order((self.bb, 1), user=self.olivia, order_status="pending_payment")            # unpaid
        self._order((self.teddy, 1), user=self.olivia, status="delivered")
        body = self.get(self.O, token=self.token).json()
        self.assertEqual(body["total"], 2)
        rows = {r["order_number"]: r for r in body["items"]}
        mine = rows[mixed.order_number]
        self.assertEqual((mine["items_count"], mine["total_cents"], mine["status"], mine["customer"]), (3, 4999 + 2 * 2299, "pending", "Olivia Martin"))
        self.assertEqual(self.get(self.O, token=self.token, tab="delivered").json()["total"], 1)
        self.assertEqual(self.get(self.O, token=self.token, q="olivia").json()["total"], 2)
        self.assertEqual(self.get(f"{self.O}/tabs", token=self.token).json(), {"all": 2, "pending": 1, "processing": 0, "shipped": 0, "delivered": 1, "cancelled": 0, "returned": 0})

    def test_detail_timeline_and_status_flow(self):
        order = self._order((self.bb, 1), (self.teddy, 2), (self.foreign, 1), user=self.olivia)
        body = self.get(f"{self.O}/{order.pk}", token=self.token).json()
        self.assertEqual(body["status"], "pending")
        self.assertEqual(sorted(body["allowed_next"]), ["cancelled", "processing"])
        self.assertEqual([i["product_name"] for i in body["items"]], ["BB Cream", "Teddy Bear"])  # not the foreign lamp
        self.assertEqual(body["summary"]["subtotal_cents"], 4999 + 2 * 2299)
        self.assertTrue(body["summary"]["multi_store"])
        self.assertEqual([s["state"] for s in body["timeline"]], ["done", "done", "waiting", "waiting", "waiting", "waiting"])
        self.assertEqual(body["customer"]["customer_key"], str(self.olivia.pk))

        for status in ("processing", "packed", "shipped"):
            res = self.post(f"{self.O}/{order.pk}/status", {"status": status, "note": f"now {status}"}, token=self.token)
            self.assertEqual(res.status_code, 200, res.content)
            self.assertEqual(res.json()["status"], status)
        order.refresh_from_db()
        self.assertEqual(order.status, "shipped")  # buyer-facing status derived from my part (other store still pending → shipped wins)
        self.assertEqual(self.post(f"{self.O}/{order.pk}/status", {"status": "processing"}, token=self.token).status_code, 409)  # no going back
        res = self.post(f"{self.O}/{order.pk}/status", {"status": "delivered"}, token=self.token)
        stages = {s["key"]: s for s in res.json()["timeline"]}
        self.assertEqual(stages["packed"]["note"], "now packed")
        self.assertTrue(all(stages[k]["state"] == "done" for k in ("processing", "packed", "shipped", "delivered")))
        order.refresh_from_db()
        self.assertEqual(order.status, "processing")  # my part delivered, the other store's part still pending → not delivered yet
        # the other store's events are invisible to me
        self.assertEqual(OrderStatusEvent.objects.filter(order=order, store=self.store).count(), 4)
        # buyer's own tracking still works and shows seller steps
        self.assertEqual(self.post("/api/v1/orders/track", {"order_number": order.order_number, "email": self.olivia.email}).status_code, 200)

    def test_cancel_and_return_restock(self):
        order = self._order((self.bb, 3), user=self.olivia)
        before = ProductVariant.objects.get(pk=self.bb.variants.first().pk).stock_qty
        res = self.post(f"{self.O}/{order.pk}/cancel", {"reason": "Out of stock"}, token=self.token)
        self.assertEqual(res.status_code, 200, res.content)
        self.assertEqual(res.json()["status"], "cancelled")
        self.assertEqual(ProductVariant.objects.get(pk=self.bb.variants.first().pk).stock_qty, before + 3)
        order.refresh_from_db()
        self.assertEqual((order.status, order.cancelled_at is not None), ("cancelled", True))
        self.assertEqual(StockMovement.objects.filter(order=order, reason="cancel").count(), 1)
        self.assertEqual(self.post(f"{self.O}/{order.pk}/cancel", {"reason": "again"}, token=self.token).status_code, 409)

        delivered = self._order((self.teddy, 1), user=self.olivia, status="delivered", order_status="delivered")
        res = self.post(f"{self.O}/{delivered.pk}/status", {"status": "returned"}, token=self.token)
        self.assertEqual(res.status_code, 200, res.content)
        delivered.refresh_from_db()
        self.assertEqual(delivered.status, "returned")
        self.assertEqual(StockMovement.objects.filter(order=delivered, reason="return").count(), 1)

    def test_overview_invoice_export(self):
        self._order((self.bb, 1), user=self.olivia)
        d = self._order((self.teddy, 1), user=self.olivia)
        self.post(f"{self.O}/{d.pk}/status", {"status": "processing"}, token=self.token)
        self.post(f"{self.O}/{d.pk}/status", {"status": "shipped"}, token=self.token)
        self.post(f"{self.O}/{d.pk}/status", {"status": "delivered"}, token=self.token)
        body = self.get(f"{self.O}/overview", token=self.token).json()
        self.assertEqual((body["total_orders"]["value"], body["pending"]["value"], body["delivered"]["value"]), (2, 1, 1))
        self.assertEqual(body["status_share"], {"pending": 50.0, "delivered": 50.0})
        self.assertEqual(len(body["monthly"]), 12)
        self.assertEqual(sum(m["values"]["delivered"] for m in body["monthly"]), 1)
        self.assertEqual(len(body["latest"]), 2)
        inv = self.get(f"{self.O}/{d.pk}/invoice", token=self.token).json()
        self.assertTrue(inv["invoice_number"].startswith(f"INV-{d.order_number}-"))
        self.assertEqual(inv["seller"]["name"], "Huda Beauty")
        res = self.get(f"{self.O}/export.csv", token=self.token)
        lines = b"".join(res.streaming_content).decode().strip().splitlines()
        self.assertEqual(len(lines), 3)
        # foreign order is 404, not 403 (no information leak)
        other = self._order((self.foreign, 1), user=self.olivia)
        self.assertEqual(self.get(f"{self.O}/{other.pk}", token=self.token).status_code, 404)


class CustomerTests(PortalBase):
    C = "/api/v1/seller/customers"

    def setUp(self):
        super().setUp()
        self.bb = make_product(self.store, self.makeup, "BB Cream", 4999, None, stock=10)
        self.foreign = make_product(self.other_store, self.makeup, "Foreign Lamp", 1000, None, stock=10)
        now = timezone.now()
        self.olivia = make_user(full_name="Olivia Martin")
        self._order((self.bb, 1), user=self.olivia, placed_at=now - timedelta(days=100))   # returning: 2 orders
        self._order((self.bb, 2), user=self.olivia, placed_at=now - timedelta(days=5))
        self._order((self.bb, 1), user=self.olivia, status="cancelled")                        # ignored
        self._order((self.bb, 1), email="daniel@example.com", name=("Daniel", "Peters"))        # guest, new
        self._order((self.bb, 1), email="EMMA@example.com", name=("Emma", "Roberts"), placed_at=now - timedelta(days=200))  # one-time, inactive
        self._order((self.foreign, 1), email="notmine@example.com")                             # other store only

    def test_list_tabs_and_types(self):
        body = self.get(self.C, token=self.token).json()
        self.assertEqual(body["total"], 3)
        rows = {r["email"]: r for r in body["items"]}
        self.assertEqual((rows[self.olivia.email]["type"], rows[self.olivia.email]["orders_count"], rows[self.olivia.email]["total_spent_cents"]), ("returning", 2, 3 * 4999))
        self.assertEqual((rows["daniel@example.com"]["type"], rows["daniel@example.com"]["status"]), ("new", "active"))
        self.assertEqual((rows["EMMA@example.com"]["type"], rows["EMMA@example.com"]["status"], rows["EMMA@example.com"]["key"]), ("one_time", "inactive", "emma@example.com"))
        self.assertEqual(body["items"][0]["name"], "Daniel Peters")  # most recent first
        self.assertEqual(self.get(f"{self.C}/tabs", token=self.token).json(), {"all": 3, "new": 1, "returning": 1, "one_time": 1})
        self.assertEqual(self.get(self.C, token=self.token, tab="returning").json()["items"][0]["name"], "Olivia Martin")
        self.assertEqual(self.get(self.C, token=self.token, q="emma").json()["total"], 1)
        self.assertEqual(self.get(self.C, token=self.token, status="inactive").json()["total"], 1)

    def test_overview(self):
        body = self.get(f"{self.C}/overview", token=self.token).json()
        self.assertEqual(body["total_customers"]["value"], 3)
        self.assertEqual(body["new_customers"]["value"], 1)          # Daniel's first order this month
        self.assertEqual(body["breakdown"]["returning"], {"count": 1, "pct": 33.3})
        self.assertEqual(len(body["monthly"]), 12)
        self.assertEqual(len(body["recent"]), 3)

    def test_detail_notes_and_export(self):
        key = str(self.olivia.pk)
        body = self.get(f"{self.C}/{key}", token=self.token).json()
        self.assertEqual(len(body["orders"]), 3)  # includes the cancelled one in history
        self.assertEqual(body["notes"], [])
        res = self.post(f"{self.C}/{key}/notes", {"body": "Prefers evening delivery"}, token=self.token)
        self.assertEqual(res.status_code, 201, res.content)
        self.assertEqual(res.json()["author"], "Sirosh Bashir")
        note_id = res.json()["id"]
        self.assertEqual(len(self.get(f"{self.C}/{key}", token=self.token).json()["notes"]), 1)
        # another store can't see the customer nor the note
        other_owner = self.other_store.owner
        self.assertEqual(self.get(f"{self.C}/{key}", token=self.token_for(other_owner)).status_code, 404)
        self.assertEqual(self.delete(f"{self.C}/{key}/notes/{note_id}", token=self.token).status_code, 204)
        self.assertEqual(self.get(f"{self.C}/nobody@example.com", token=self.token).status_code, 404)
        res = self.get(f"{self.C}/export.csv", token=self.token)
        lines = b"".join(res.streaming_content).decode().strip().splitlines()
        self.assertEqual(len(lines), 4)


class DashboardTests(PortalBase):
    def test_summary_and_search(self):
        from catalog.services import refresh_stock_status

        bb = make_product(self.store, self.makeup, "BB Cream", 4999, None, stock=2)
        lip = make_product(self.store, self.makeup, "Lipstick", 1299, None, stock=0)
        for p in (bb, lip):
            refresh_stock_status(p)
        olivia = make_user(full_name="Olivia Martin")
        self.client.get(f"/api/v1/products/{bb.slug}", headers={"X-Cart-Token": "g1"})
        self.client.get(f"/api/v1/products/{bb.slug}", headers={"X-Cart-Token": "g2"})
        o1 = self._order((bb, 2), user=olivia)
        self._order((lip, 1), user=olivia)
        self._order((bb, 1), user=olivia, status="cancelled")
        for status in ("processing", "shipped", "delivered"):
            self.post(f"/api/v1/seller/orders/{o1.pk}/status", {"status": status}, token=self.token)

        body = self.get("/api/v1/seller/dashboard", token=self.token).json()
        self.assertEqual(body["seller"]["first_name"], "Sirosh")
        self.assertEqual(body["store"]["name"], "Huda Beauty")
        self.assertEqual(body["sales_cents"]["value"], 2 * 4999 + 1299)      # booked (cancelled excluded)
        self.assertEqual(body["revenue_cents"]["value"], 2 * 4999)           # delivered only
        self.assertEqual(body["orders"]["value"], 3)                          # includes the cancelled order (it was placed)
        self.assertEqual((body["products"]["value"], body["low_stock"]), (2, 1))
        self.assertEqual(body["stock_breakdown"], {"in_stock": 0, "low_stock": 1, "out_of_stock": 1})
        perf = body["performance"]
        self.assertEqual(perf["product_views"]["value"], 2)
        self.assertEqual(perf["conversion_rate"]["value"], 150.0)
        self.assertEqual(perf["aov_cents"]["value"], round((2 * 4999 + 1299) / 3))
        self.assertEqual(perf["customer_growth"]["value"], 1)
        self.assertEqual(len(body["revenue_series"]), 12)
        self.assertEqual(sum(m["values"]["revenue"] for m in body["revenue_series"]), 2 * 4999)
        self.assertEqual(len(body["latest_orders"]), 3)

        hits = self.get("/api/v1/seller/search", token=self.token, q="oliv").json()
        self.assertEqual((len(hits["products"]), len(hits["orders"]), len(hits["customers"])), (0, 3, 1))
        self.assertEqual(self.get("/api/v1/seller/search", token=self.token, q="lip").json()["products"][0]["label"], "Lipstick")
        self.assertEqual(self.get("/api/v1/seller/search", token=self.token, q=o1.order_number).json()["orders"][0]["label"], o1.order_number)

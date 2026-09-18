"""Seller portal: products wizard + review, inventory, orders, customers, dashboard."""

import io

from django.core.files.uploadedfile import SimpleUploadedFile
from PIL import Image

from catalog.models import Brand, Category, Product, ProductVariant, StockMovement
from core.testing import ApiTestCase, make_category, make_product, make_store, make_user
from orders.models import Order, OrderItem
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
        cls.staff = make_user(is_staff=True)

    def setUp(self):
        self.token = self.token_for(self.owner)

    def _order(self, *lines, user=None, store=None, status="pending", order_status=Order.OrderStatus.CONFIRMED,
               email=None, name=("Olivia", "Martin"), payment="card", placed_at=None) -> Order:
        """lines: (product, qty). Creates a paid buyer order with this store's items."""
        store = store or self.store
        user = user if user is not None else None
        order = Order.objects.create(
            user=user, status=order_status, order_number=f"ASK{Order.objects.count() + 1:05d}",
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

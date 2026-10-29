"""A product is live only as a reviewer approved it: edit locks while under review, edits after approval, the archive route,
who decided, category counts, the double submit, and what a seller may not set (featured flags, currency)."""

from django.test import override_settings
from ninja.errors import HttpError

from catalog.models import Category, Product
from catalog.review import content_hash
from core.testing import make_product
from notifications.models import Notification
from portal import products as svc
from portal.tests import P, PortalBase, png


class ReviewTrailBase(PortalBase):
    def auth(self):
        return {"Authorization": f"Bearer {self.token}"}

    def draft(self, name="NARS BB Cream"):
        """A complete draft (picture, price, stock), not yet submitted."""
        pid = self.post(P, {"name": name, "category_id": str(self.makeup.pk), "tags": ["spf"], "short_description": "Tinted"}, token=self.token).json()["id"]
        self.client.post(f"{P}/{pid}/images", {"file": png()}, headers=self.auth())
        self.patch(f"{P}/{pid}", {"price_cents": 2599, "description": "Light coverage"}, token=self.token)
        vid = self.get(f"{P}/{pid}", token=self.token).json()["variants"][0]["id"]
        self.patch(f"{P}/{pid}/variants/{vid}", {"stock_qty": 5}, token=self.token)
        return pid

    def submitted(self, name="NARS BB Cream"):
        pid = self.draft(name)
        self.assertEqual(self.post(f"{P}/{pid}/status", {"action": "submit"}, token=self.token).status_code, 200)
        return pid

    def live(self, name="NARS BB Cream"):
        pid = self.submitted(name)
        self.assertEqual(self.post(f"/api/v1/admin/products/{pid}/approve", token=self.staff_token).status_code, 200)
        return pid

    @property
    def staff_token(self):
        return self.token_for(self.staff)

    def edited_ids(self):
        return [r["id"] for r in self.get("/api/v1/admin/products", token=self.staff_token, status="edited").json()["items"]]

    def public_status(self, pid):
        return self.get(f"/api/v1/products/{Product.objects.get(pk=pid).slug}").status_code


class UnderReviewLockTests(ReviewTrailBase):
    def test_nothing_a_reviewer_reads_can_change_while_the_product_is_pending(self):
        pid = self.submitted()
        body = self.get(f"{P}/{pid}", token=self.token).json()
        image, variant = body["images"][0]["id"], body["variants"][0]["id"]
        locked = [
            self.patch(f"{P}/{pid}", {"name": "Changed"}, token=self.token),
            self.client.post(f"{P}/{pid}/images", {"file": png("b.png")}, headers=self.auth()),
            self.client.delete(f"{P}/{pid}/images/{image}", headers=self.auth()),
            self.put(f"{P}/{pid}/images/order", {"image_ids": [image]}, token=self.token),
            self.put(f"{P}/{pid}/content", {"sections": [{"section": "benefit", "title": "x", "body": "y"}]}, token=self.token),
            self.put(f"{P}/{pid}/options", {"options": [{"name": "Size", "values": [{"value": "S"}]}]}, token=self.token),
            self.patch(f"{P}/{pid}/variants/{variant}", {"price_cents": 100}, token=self.token),
        ]
        self.assertEqual([r.status_code for r in locked], [409] * len(locked), [r.content for r in locked])

    def test_stock_and_sku_stay_editable_because_a_review_does_not_read_them(self):
        pid = self.submitted()
        variant = self.get(f"{P}/{pid}", token=self.token).json()["variants"][0]["id"]
        self.assertEqual(self.patch(f"{P}/{pid}/variants/{variant}", {"stock_qty": 9, "sku": "BB-01"}, token=self.token).status_code, 200)

    def test_a_double_submit_is_refused_instead_of_acting_on_a_stale_status(self):
        pid = self.draft()
        first, second = svc.get_product(self.store, pid), svc.get_product(self.store, pid)
        svc.change_status(first, "submit")
        with self.assertRaises(HttpError) as caught:
            svc.change_status(second, "submit")          # `second` still believes it is a draft
        self.assertEqual(caught.exception.status_code, 409)


class EditAfterApprovalTests(ReviewTrailBase):
    def test_editing_a_live_product_keeps_it_live_and_lists_it_for_staff(self):
        pid = self.live()
        self.assertEqual(self.edited_ids(), [])
        self.assertEqual(self.patch(f"{P}/{pid}", {"description": "Now with a miracle cure"}, token=self.token).json()["status"], "active")
        self.assertEqual(self.edited_ids(), [pid])
        row = self.get("/api/v1/admin/products", token=self.staff_token, status="edited").json()["items"][0]
        self.assertEqual((row["edited"], row["status"]), (True, "active"))
        self.assertEqual(self.public_status(pid), 200)                         # the default policy: flag, do not hide

    def test_what_a_review_does_not_read_never_raises_the_flag(self):
        pid = self.live()
        variant = self.get(f"{P}/{pid}", token=self.token).json()["variants"][0]["id"]
        self.patch(f"{P}/{pid}/variants/{variant}", {"stock_qty": 50, "sku": "BB-NEW"}, token=self.token)
        self.patch(f"{P}/{pid}", {"weight_grams": 300, "ships_from": "Durban", "cost_price_cents": 900, "handling_days": 3}, token=self.token)
        self.patch(f"{P}/{pid}", {"price_cents": 2500}, token=self.token)          # same price band: a typo fix
        self.assertEqual(self.edited_ids(), [])

    def test_a_price_that_doubles_or_a_new_picture_is_a_change_a_reviewer_must_see(self):
        pid = self.live()
        self.patch(f"{P}/{pid}", {"price_cents": 5400}, token=self.token)
        self.assertEqual(self.edited_ids(), [pid])
        other = self.live("Second cream")
        self.client.post(f"{P}/{other}/images", {"file": png("c.png")}, headers=self.auth())
        self.assertIn(other, self.edited_ids())

    def test_putting_the_words_back_clears_the_flag(self):
        pid = self.live()
        self.patch(f"{P}/{pid}", {"description": "Something else"}, token=self.token)
        self.assertEqual(self.edited_ids(), [pid])
        self.patch(f"{P}/{pid}", {"description": "Light coverage"}, token=self.token)
        self.assertEqual(self.edited_ids(), [])

    def test_staff_can_approve_the_edit_again_without_telling_the_seller_it_is_live(self):
        pid = self.live()
        self.patch(f"{P}/{pid}", {"description": "New text"}, token=self.token)
        told = Notification.objects.filter(store=self.store, kind="product_approved").count()
        self.assertEqual(self.post(f"/api/v1/admin/products/{pid}/approve", token=self.staff_token).status_code, 200)
        self.assertEqual(self.edited_ids(), [])
        self.assertEqual(Notification.objects.filter(store=self.store, kind="product_approved").count(), told)
        self.patch(f"{P}/{pid}", {"description": "New text"}, token=self.token)       # the same words are now the approved ones
        self.assertEqual(self.edited_ids(), [])

    def test_staff_can_take_an_edited_live_product_down_with_a_reason(self):
        pid = self.live()
        self.patch(f"{P}/{pid}", {"description": "Not allowed"}, token=self.token)
        self.assertEqual(self.post(f"/api/v1/admin/products/{pid}/reject", {"reason": "Medical claim"}, token=self.staff_token).status_code, 200)
        body = self.get(f"{P}/{pid}", token=self.token).json()
        self.assertEqual((body["status"], body["rejection_reason"]), ("rejected", "Medical claim"))
        self.assertEqual(self.public_status(pid), 404)
        self.assertTrue(Notification.objects.filter(store=self.store, kind="product_rejected").exists())

    def test_an_unedited_live_product_cannot_be_approved_or_rejected_again(self):
        pid = self.live()
        self.assertEqual(self.post(f"/api/v1/admin/products/{pid}/approve", token=self.staff_token).status_code, 409)
        self.assertEqual(self.post(f"/api/v1/admin/products/{pid}/reject", {"reason": "x"}, token=self.staff_token).status_code, 409)

    @override_settings(PRODUCT_EDIT_POLICY="hold")
    def test_under_the_hold_policy_an_edited_live_product_goes_back_under_review(self):
        pid = self.live()
        self.assertEqual(self.patch(f"{P}/{pid}", {"description": "New text"}, token=self.token).json()["status"], "pending")
        self.assertEqual(self.public_status(pid), 404)
        self.assertEqual(self.post(f"/api/v1/admin/products/{pid}/approve", token=self.staff_token).status_code, 200)
        self.assertEqual(self.public_status(pid), 200)

    def test_a_product_published_before_the_record_existed_adopts_what_it_said_before_the_first_edit(self):
        old = make_product(self.store, self.makeup, "Legacy cream")             # active, never reviewed through the new flow
        self.assertEqual(old.reviewed_hash, "")
        variant = old.variants.first()
        self.patch(f"{P}/{old.pk}/variants/{variant.pk}", {"stock_qty": 3}, token=self.token)
        self.assertEqual(self.edited_ids(), [])
        self.patch(f"{P}/{old.pk}", {"description": "Rewritten"}, token=self.token)
        self.assertEqual(self.edited_ids(), [str(old.pk)])
        self.patch(f"{P}/{old.pk}", {"description": ""}, token=self.token)
        self.assertEqual(self.edited_ids(), [])


class ArchiveRouteTests(ReviewTrailBase):
    def test_archive_and_unarchive_without_changes_comes_straight_back(self):
        pid = self.live()
        self.post(f"{P}/{pid}/status", {"action": "archive"}, token=self.token)
        self.assertEqual(self.public_status(pid), 404)
        self.assertEqual(self.post(f"{P}/{pid}/status", {"action": "unarchive"}, token=self.token).json()["status"], "active")

    def test_a_product_changed_while_archived_comes_back_through_review(self):
        pid = self.live()
        self.post(f"{P}/{pid}/status", {"action": "archive"}, token=self.token)
        self.patch(f"{P}/{pid}", {"description": "Changed in the archive"}, token=self.token)
        body = self.post(f"{P}/{pid}/status", {"action": "unarchive"}, token=self.token).json()
        self.assertEqual(body["status"], "pending")
        self.assertEqual(self.public_status(pid), 404)
        self.assertEqual(self.post(f"/api/v1/admin/products/{pid}/approve", token=self.staff_token).status_code, 200)
        self.assertEqual(self.public_status(pid), 200)


class DecisionTrailTests(ReviewTrailBase):
    def test_an_approval_records_who_when_and_what_was_approved_and_tells_the_seller_once(self):
        pid = self.live()
        product = Product.objects.get(pk=pid)
        self.assertEqual((product.review_source, product.reviewed_by_id, product.edited_since_review), ("staff", self.staff.pk, False))
        self.assertIsNotNone(product.reviewed_at)
        self.assertEqual(product.reviewed_hash, content_hash(product))
        self.assertEqual(Notification.objects.filter(store=self.store, kind="product_approved").count(), 1)

    def test_a_rejection_records_who_and_tells_the_seller_once(self):
        pid = self.submitted()
        self.post(f"/api/v1/admin/products/{pid}/reject", {"reason": "Blurry"}, token=self.staff_token)
        product = Product.objects.get(pk=pid)
        self.assertEqual((product.review_source, product.reviewed_by_id), ("staff", self.staff.pk))
        self.assertEqual(Notification.objects.filter(store=self.store, kind="product_rejected").count(), 1)

    def test_the_content_hash_ignores_spacing_and_case_and_notices_real_changes(self):
        pid = self.live()
        product = Product.objects.get(pk=pid)
        before = content_hash(product)
        product.name = "  nars   BB CREAM "
        self.assertEqual(content_hash(product), before)
        product.tags = ["SPF"]
        self.assertEqual(content_hash(product), before)
        product.tags = ["spf", "vegan"]
        self.assertNotEqual(content_hash(product), before)


class CategoryCountTests(ReviewTrailBase):
    def count(self):
        return Category.objects.get(pk=self.makeup.pk).product_counts.get(self.store.country_id, 0)

    def test_the_count_follows_every_way_in_or_out_of_the_shop_window(self):
        pid = self.live()
        self.assertEqual(self.count(), 1)
        self.post(f"{P}/{pid}/status", {"action": "archive"}, token=self.token)
        self.assertEqual(self.count(), 0)
        self.post(f"{P}/{pid}/status", {"action": "unarchive"}, token=self.token)
        self.assertEqual(self.count(), 1)
        self.post(f"{P}/{pid}/status", {"action": "unpublish"}, token=self.token)
        self.assertEqual(self.count(), 0)

    def test_taking_down_an_edited_live_product_updates_the_count(self):
        pid = self.live()
        self.patch(f"{P}/{pid}", {"description": "Nope"}, token=self.token)
        self.post(f"/api/v1/admin/products/{pid}/reject", {"reason": "x"}, token=self.staff_token)
        self.assertEqual(self.count(), 0)


class SellerCannotSetTests(ReviewTrailBase):
    def test_a_seller_cannot_put_their_own_product_on_the_landing_page(self):
        pid = self.live()
        res = self.patch(f"{P}/{pid}", {"is_featured": True, "is_new_arrival": True}, token=self.token)
        self.assertEqual(res.status_code, 200)
        product = Product.objects.get(pk=pid)
        self.assertEqual((product.is_featured, product.is_new_arrival), (False, False))
        self.assertEqual(self.get("/api/v1/products/featured").json(), [])

    def test_prices_are_in_the_currency_of_the_stores_market(self):
        pid = self.draft()
        currency = self.store.country.currency
        wrong = "USD" if currency != "USD" else "EUR"
        self.assertEqual(self.patch(f"{P}/{pid}", {"currency": wrong}, token=self.token).status_code, 422)
        self.assertEqual(self.patch(f"{P}/{pid}", {"currency": currency.lower()}, token=self.token).status_code, 200)
        self.assertEqual(Product.objects.get(pk=pid).currency, currency)


class DuplicateTests(ReviewTrailBase):
    def test_a_copy_of_a_reviewed_product_is_unreviewed(self):
        pid = self.live()
        self.patch(f"{P}/{pid}", {"description": "Edited after approval"}, token=self.token)
        copy = self.post(f"{P}/{pid}/duplicate", token=self.token).json()
        product = Product.objects.get(pk=copy["id"])
        self.assertEqual((product.status, product.reviewed_hash, product.edited_since_review, product.review_source), ("draft", "", False, ""))
        self.assertEqual((product.reviewed_by_id, product.reviewed_at), (None, None))
        self.assertEqual(copy["completion"]["ready_to_submit"], False)           # no pictures are copied

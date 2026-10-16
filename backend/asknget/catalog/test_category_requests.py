"""Category requests: a seller asks, staff approve (creating the category, or pointing at an existing one) or reject,
and the seller is told. A seller can never create a category directly."""

from io import BytesIO

from django.core.files.uploadedfile import SimpleUploadedFile
from PIL import Image

from catalog.models import Category, CategoryRequest
from core.testing import ApiTestCase, make_category, make_store, make_user, make_staff
from notifications.models import Notification as StoreNotification

SELLER = "/api/v1/seller/category-requests"
ADMIN = "/api/v1/admin/category-requests"


def png(name="proof.png", size=(20, 20)) -> SimpleUploadedFile:
    buf = BytesIO()
    Image.new("RGB", size, "orange").save(buf, "PNG")
    return SimpleUploadedFile(name, buf.getvalue(), content_type="image/png")


class CategoryRequestTestCase(ApiTestCase):
    @classmethod
    def setUpTestData(cls):
        cls.electronics = make_category("Electronics")
        cls.phones = make_category("Mobile Phones", parent=cls.electronics)
        cls.owner = make_user(is_seller=True, full_name="Sirosh Bashir")
        cls.store = make_store(owner=cls.owner, name="Huda Beauty")
        cls.other_owner = make_user(is_seller=True)
        cls.other_store = make_store(owner=cls.other_owner, name="Other Co")
        cls.staff = make_staff()

    def setUp(self):
        self.token = self.token_for(self.owner)

    def ask(self, name="Smart Home", reason="We sell smart plugs and bulbs", parent=None, document=None, token=None):
        data = {"name": name, "reason": reason}
        if parent is not None:
            data["parent_id"] = str(parent.pk)
        if document is not None:
            data["document"] = document
        return self.client.post(SELLER, data, headers={"Authorization": f"Bearer {token or self.token}"})

    def staff_token(self):
        return self.token_for(self.staff)


class SellerSideTests(CategoryRequestTestCase):
    def test_a_seller_asks_with_a_supporting_image(self):
        res = self.ask(parent=self.electronics, document=png())
        self.assertEqual(res.status_code, 201, res.content)
        body = res.json()
        self.assertEqual((body["name"], body["status"], body["parent"]["name"]), ("Smart Home", "pending", "Electronics"))
        self.assertEqual(body["document_name"], "proof.png")
        self.assertIsNone(body["category"])
        self.assertFalse(Category.objects.filter(name="Smart Home").exists())                  # nothing is created by asking
        image = self.client.get(body["document_url"])
        self.assertEqual((image.status_code, image["Content-Type"], image["X-Content-Type-Options"]), (200, "image/png", "nosniff"))
        tampered = body["document_url"].replace("?t=", "?t=x")
        self.assertEqual(self.client.get(tampered).status_code, 404)

    def test_the_image_is_optional_and_a_top_level_category_can_be_asked_for(self):
        body = self.ask().json()
        self.assertIsNone(body["parent"])
        self.assertIsNone(body["document_url"])

    def test_a_name_that_already_exists_points_to_it(self):
        res = self.ask(name="mobile phones")
        self.assertEqual(res.status_code, 409)
        self.assertEqual(res.json()["detail"], "category_exists")
        self.assertEqual(res.json()["category"]["id"], str(self.phones.pk))
        self.assertEqual(self.ask(name="Mobile-Phones").status_code, 409)                      # same slug

    def test_the_same_request_twice_is_refused_but_another_store_may_ask_too(self):
        self.ask()
        self.assertEqual(self.ask(name="smart home").json()["detail"], "already_requested")
        self.assertEqual(self.ask(token=self.token_for(self.other_owner)).status_code, 201)

    def test_the_parent_must_be_a_top_level_category_that_exists(self):
        self.assertEqual(self.ask(parent=self.phones).json()["detail"], "parent_must_be_top_level")
        missing = Category(pk="00000000-0000-0000-0000-000000000000")
        self.assertEqual(self.ask(parent=missing).json()["detail"], "unknown_parent")

    def test_only_real_small_png_or_jpg_files(self):
        fake = SimpleUploadedFile("proof.png", b"<html>not an image</html>", content_type="image/png")
        self.assertEqual(self.ask(document=fake).status_code, 422)
        big = SimpleUploadedFile("big.png", b"0" * (1024 * 1024 + 1), content_type="image/png")
        self.assertEqual(self.ask(name="Big", document=big).status_code, 422)
        self.assertEqual(CategoryRequest.objects.count(), 0)

    def test_name_and_reason_are_required(self):
        self.assertEqual(self.ask(name="").status_code, 422)
        self.assertEqual(self.ask(reason="").status_code, 422)
        self.assertEqual(self.ask(reason="x" * 251).status_code, 422)

    def test_at_most_five_waiting_requests(self):
        for i in range(5):
            self.assertEqual(self.ask(name=f"Thing {i}").status_code, 201)
        self.assertEqual(self.ask(name="Thing 5").status_code, 429)

    def test_withdraw_while_waiting(self):
        req = self.ask().json()
        res = self.delete(f"{SELLER}/{req['id']}", self.token)
        self.assertEqual((res.status_code, res.json()["status"]), (200, "withdrawn"))
        self.assertEqual(self.delete(f"{SELLER}/{req['id']}", self.token).status_code, 409)
        self.assertEqual(self.ask().status_code, 201)                                           # may ask again after withdrawing

    def test_a_seller_sees_only_their_own_requests(self):
        mine = self.ask().json()
        theirs = self.ask(name="Garden", token=self.token_for(self.other_owner)).json()
        self.assertEqual([r["id"] for r in self.get(SELLER, self.token).json()["items"]], [mine["id"]])
        self.assertEqual(self.get(f"{SELLER}/{theirs['id']}", self.token).status_code, 404)
        self.assertEqual(self.delete(f"{SELLER}/{theirs['id']}", self.token).status_code, 404)

    def test_buyers_cannot_ask_and_sellers_cannot_decide(self):
        buyer = make_user()
        self.assertEqual(self.ask(token=self.token_for(buyer)).status_code, 403)
        req = self.ask().json()
        self.assertEqual(self.post(f"{ADMIN}/{req['id']}/approve", {}, self.token).status_code, 403)
        self.assertEqual(self.get(ADMIN, self.token).status_code, 403)


class StaffSideTests(CategoryRequestTestCase):
    def decide(self, req_id, action, body):
        with self.captureOnCommitCallbacks(execute=True):
            return self.post(f"{ADMIN}/{req_id}/{action}", body, self.staff_token())

    def test_approving_creates_the_category_and_tells_the_seller(self):
        req = self.ask(parent=self.electronics).json()
        res = self.decide(req["id"], "approve", {})
        self.assertEqual(res.status_code, 200, res.content)
        body = res.json()
        self.assertEqual((body["status"], body["created_category"], body["category"]["name"]), ("approved", True, "Smart Home"))
        category = Category.objects.get(name="Smart Home")
        self.assertEqual((category.parent, category.is_active, category.slug), (self.electronics, True, "smart-home"))
        note = StoreNotification.objects.get(store=self.store, kind="category_request_approved")
        self.assertEqual(note.params, {"name": "Smart Home", "category": "Smart Home"})
        tree = self.get("/api/v1/categories").json()
        electronics = next(c for c in tree if c["name"] == "Electronics")
        self.assertIn("Smart Home", [c["name"] for c in electronics["children"]])
        self.assertEqual(self.get(f"{SELLER}/{req['id']}", self.token).json()["category"]["id"], str(category.pk))

    def test_staff_can_rename_and_move_it(self):
        req = self.ask(parent=self.electronics).json()
        body = self.decide(req["id"], "approve", {"name": "Smart Home Devices", "parent_id": None}).json()
        category = Category.objects.get(pk=body["category"]["id"])
        self.assertEqual((category.name, category.parent_id), ("Smart Home Devices", None))
        self.assertEqual(body["name"], "Smart Home")                                            # what was asked stays on record

    def test_staff_can_point_the_request_at_an_existing_category(self):
        req = self.ask(name="Cell Phones", parent=self.electronics).json()
        before = Category.objects.count()
        body = self.decide(req["id"], "approve", {"use_category_id": str(self.phones.pk), "note": "We call it Mobile Phones"}).json()
        self.assertEqual((body["created_category"], body["category"]["id"], body["review_note"]), (False, str(self.phones.pk), "We call it Mobile Phones"))
        self.assertEqual(Category.objects.count(), before)

    def test_approving_a_name_that_appeared_meanwhile_says_so(self):
        req = self.ask().json()
        existing = make_category("Smart Home")                                                  # e.g. another store's request was approved
        queue = self.get(ADMIN, self.staff_token()).json()["items"]
        self.assertEqual(queue[0]["existing_category"]["id"], str(existing.pk))
        res = self.decide(req["id"], "approve", {})
        self.assertEqual((res.status_code, res.json()["detail"]), (409, "category_exists"))
        self.assertEqual(CategoryRequest.objects.get(pk=req["id"]).status, "pending")

    def test_rejecting_needs_a_reason_and_tells_the_seller(self):
        req = self.ask().json()
        self.assertEqual(self.decide(req["id"], "reject", {"reason": ""}).status_code, 422)
        body = self.decide(req["id"], "reject", {"reason": "Use Electronics > Accessories"}).json()
        self.assertEqual((body["status"], body["review_note"]), ("rejected", "Use Electronics > Accessories"))
        note = StoreNotification.objects.get(store=self.store, kind="category_request_rejected")
        self.assertEqual(note.params["reason"], "Use Electronics > Accessories")
        self.assertFalse(Category.objects.filter(name="Smart Home").exists())

    def test_a_request_is_decided_once(self):
        req = self.ask().json()
        self.decide(req["id"], "reject", {"reason": "Not needed"})
        self.assertEqual(self.decide(req["id"], "approve", {}).json()["detail"], "not_pending")
        self.delete(f"{SELLER}/{self.ask(name='Garden').json()['id']}", self.token)
        withdrawn = CategoryRequest.objects.get(name="Garden")
        self.assertEqual(self.decide(withdrawn.pk, "approve", {}).status_code, 409)

    def test_the_queue_is_oldest_first_and_filters(self):
        first = self.ask(name="Alpha").json()
        second = self.ask(name="Beta", token=self.token_for(self.other_owner)).json()
        token = self.staff_token()
        rows = self.get(ADMIN, token).json()["items"]
        self.assertEqual([r["id"] for r in rows], [first["id"], second["id"]])
        self.assertEqual(rows[0]["store"]["name"], "Huda Beauty")
        self.assertEqual(rows[0]["requested_by"], self.owner.email)
        self.assertEqual([r["name"] for r in self.get(ADMIN, token, q="other co").json()["items"]], ["Beta"])
        self.assertEqual(self.decide(first["id"], "reject", {"reason": "Not needed"}).status_code, 200)
        self.assertEqual([r["name"] for r in self.get(ADMIN, token, status="rejected").json()["items"]], ["Alpha"])
        self.assertEqual(self.get(f"{ADMIN}/{second['id']}", token).json()["name"], "Beta")

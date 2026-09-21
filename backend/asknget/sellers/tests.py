"""End-to-end tests for the seller onboarding flow and admin KYC review."""

import tempfile

from django.conf import settings
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings

from accounts.models import User
from catalog.models import Category
from core.models import Bank

from .models import SellerApplication, SellerDocument, Store

AUTH = "/api/v1/auth"
SELLER = "/api/v1/seller"
ADMIN = "/api/v1/admin/seller-applications"
PDF = b"%PDF-1.4 fake"


@override_settings(MEDIA_ROOT=tempfile.mkdtemp())
class SellerTestCase(TestCase):
    def setUp(self):
        self.bank = Bank.objects.create(name="Meezan Bank", country="PK")
        self.cat_beauty = Category.objects.create(name="Beauty", slug="beauty")
        self.cat_fashion = Category.objects.create(name="Fashion", slug="fashion")
        self.staff = User.objects.create_user(email="admin@asknget.com", password="Str0ngPassw0rd!", is_staff=True)
        self.staff_token = self.login("admin@asknget.com")

    # --- http helpers ---------------------------------------------------------

    def _h(self, token):
        return {"Authorization": f"Bearer {token}"}

    def post(self, url, data=None, token=None):
        return self.client.post(url, data=data or {}, content_type="application/json", headers=self._h(token) if token else {})

    def patch(self, url, data, token):
        return self.client.patch(url, data=data, content_type="application/json", headers=self._h(token))

    def get(self, url, token, **params):
        return self.client.get(url, params, headers=self._h(token))

    def delete(self, url, token):
        return self.client.delete(url, headers=self._h(token))

    def login(self, email, password="Str0ngPassw0rd!"):
        res = self.post(f"{AUTH}/login", {"email": email, "password": password})
        self.assertEqual(res.status_code, 200, res.content)
        return res.json()["tokens"]["access_token"]

    _phone_seq = 0

    def register_seller(self, email="huda@example.com", account_type="registered_company"):
        SellerTestCase._phone_seq += 1
        res = self.post(f"{AUTH}/seller/register", {
            "email": email, "phone": f"+27 82 {SellerTestCase._phone_seq:07d}", "password": "Str0ngPassw0rd!", "confirm_password": "Str0ngPassw0rd!",
            "account_type": account_type, "accept_terms": True,
        })
        self.assertEqual(res.status_code, 201, res.content)
        return res.json()["tokens"]["access_token"]

    # --- flow helpers ---------------------------------------------------------

    BASIC = {
        "first_name": "Ahmad", "last_name": "Khan", "date_of_birth": "1990-05-01", "id_passport_number": "AB1234567",
        "phone": "+923001234567", "email": "ahmad.khan@gmail.com", "residential_address": "12 Mall Rd",
        "residential_postal_code": "54000", "residential_city": "Lahore", "residential_province": "Punjab",
    }
    BUSINESS = {
        "legal_business_name": "Huda Beauty (Pty) Ltd", "store_display_name": "Huda Beauty", "business_type": "private_limited",
        "company_registration_number": "2020/123456/07", "business_email": "hello@hudabeauty.com", "business_phone": "+27111234567",
        "business_address": "25 Market Street", "business_city": "Johannesburg", "business_province": "Gauteng",
        "business_postal_code": "2001", "store_description": "Premium beauty products.",
    }
    REP = {
        "rep_first_name": "Ahmad", "rep_last_name": "Khan", "rep_role": "director", "rep_id_passport_number": "AB1234567",
        "rep_mobile": "+923001234567", "rep_email": "ahmad.khan@gmail.com",
    }

    def upload(self, token, doc_type, name="doc.pdf"):
        res = self.client.post(
            f"{SELLER}/application/documents",
            {"type": doc_type, "file": SimpleUploadedFile(name, PDF, content_type="application/pdf")},
            headers=self._h(token),
        )
        self.assertEqual(res.status_code, 201, res.content)
        return res.json()

    def fill_all(self, token, account_type="registered_company"):
        self.patch(f"{SELLER}/application/steps/basic-info", self.BASIC, token)
        self.patch(f"{SELLER}/application/steps/business-info", {**self.BUSINESS, "category_ids": [str(self.cat_beauty.id)]}, token)
        if account_type != "individual":
            self.patch(f"{SELLER}/application/steps/representative", self.REP, token)
        self.patch(f"{SELLER}/application/steps/banking", {
            "bank_id": str(self.bank.id), "bank_account_type": "business", "account_holder_name": "Huda Beauty",
            "account_number": "001234567891234", "branch_code": "250655",
        }, token)
        for d in ("id_passport", "bank_confirmation", "proof_of_address") + (("company_registration",) if account_type != "individual" else ()):
            self.upload(token, d)

    def submit(self, token):
        res = self.post(f"{SELLER}/application/submit", {"confirm_accuracy": True, "accept_terms": True}, token)
        self.assertEqual(res.status_code, 200, res.content)
        return res.json()

    def app_id(self, token):
        return self.get(f"{SELLER}/application", token).json()["id"]


class WizardTests(SellerTestCase):
    def test_registration_creates_draft_application(self):
        token = self.register_seller()
        body = self.get(f"{SELLER}/application", token).json()
        self.assertEqual(body["status"], "draft")
        self.assertEqual(body["account_type"], "registered_company")
        self.assertEqual(body["current_step"], 1)
        self.assertTrue(body["editable"])
        self.assertFalse(body["can_submit"])
        self.assertEqual([s["complete"] for s in body["steps"]], [False, False, False, False])

    def test_customer_cannot_access_seller_portal(self):
        res = self.post(f"{AUTH}/register", {"email": "c@example.com", "password": "Str0ngPassw0rd!", "confirm_password": "Str0ngPassw0rd!"})
        token = res.json()["tokens"]["access_token"]
        self.assertEqual(self.get(f"{SELLER}/application", token).status_code, 403)

    def test_step_autosave_is_partial_and_reports_missing(self):
        token = self.register_seller()
        res = self.patch(f"{SELLER}/application/steps/basic-info", {"first_name": "Ahmad", "phone": "+92 300 1234567"}, token)
        self.assertEqual(res.status_code, 200, res.content)
        body = res.json()
        self.assertEqual(body["basic_info"]["first_name"], "Ahmad")
        self.assertEqual(body["basic_info"]["phone"], "+923001234567")
        self.assertEqual(body["current_step"], 2)
        step1 = body["steps"][0]
        self.assertFalse(step1["complete"])
        self.assertIn("last_name", step1["missing_fields"])
        self.assertNotIn("first_name", step1["missing_fields"])
        self.assertIn("id_passport", step1["missing_documents"])
        # second partial save keeps earlier values
        self.patch(f"{SELLER}/application/steps/basic-info", {"last_name": "Khan"}, token)
        body = self.get(f"{SELLER}/application", token).json()
        self.assertEqual(body["basic_info"]["first_name"], "Ahmad")
        self.assertEqual(body["basic_info"]["last_name"], "Khan")

    def test_sensitive_fields_are_masked_for_seller(self):
        token = self.register_seller()
        self.patch(f"{SELLER}/application/steps/basic-info", {"id_passport_number": "AB1234567"}, token)
        self.patch(f"{SELLER}/application/steps/banking", {"account_number": "001234567891234"}, token)
        body = self.get(f"{SELLER}/application", token).json()
        self.assertEqual(body["basic_info"]["id_passport_number_masked"], "•••••4567")
        self.assertEqual(body["banking"]["account_number_last4"], "1234")
        self.assertNotIn("account_number", body["banking"])
        app = SellerApplication.objects.get()
        self.assertEqual(app.account_number, "001234567891234")  # decrypts transparently

    def test_business_step_validates_categories_and_addresses(self):
        token = self.register_seller()
        res = self.patch(f"{SELLER}/application/steps/business-info", {"category_ids": ["00000000-0000-0000-0000-000000000000"]}, token)
        self.assertEqual(res.status_code, 422)
        res = self.patch(f"{SELLER}/application/steps/business-info", {"pickup_same_as_business": False}, token)
        self.assertEqual(res.status_code, 422)
        self.assertIn("pickup_address", res.json()["detail"])
        res = self.patch(f"{SELLER}/application/steps/business-info", {"category_ids": [str(self.cat_beauty.id), str(self.cat_fashion.id)]}, token)
        self.assertEqual(res.status_code, 200, res.content)
        self.assertEqual({c["slug"] for c in res.json()["business_info"]["categories"]}, {"beauty", "fashion"})

    def test_banking_step_rejects_unknown_bank(self):
        token = self.register_seller()
        res = self.patch(f"{SELLER}/application/steps/banking", {"bank_id": "00000000-0000-0000-0000-000000000000"}, token)
        self.assertEqual(res.status_code, 422)

    def test_directors_crud(self):
        token = self.register_seller()
        res = self.post(f"{SELLER}/application/directors", {"first_name": "Sara", "last_name": "Ali", "role": "director"}, token)
        self.assertEqual(res.status_code, 201, res.content)
        did = res.json()["id"]
        res = self.patch(f"{SELLER}/application/directors/{did}", {"first_name": "Sarah", "last_name": "Ali"}, token)
        self.assertEqual(res.json()["first_name"], "Sarah")
        self.assertEqual(len(self.get(f"{SELLER}/application", token).json()["directors"]), 1)
        self.assertEqual(self.delete(f"{SELLER}/application/directors/{did}", token).status_code, 204)
        self.assertEqual(len(self.get(f"{SELLER}/application", token).json()["directors"]), 0)

    def test_document_upload_validates_and_versions(self):
        token = self.register_seller()
        bad = self.client.post(f"{SELLER}/application/documents",
                               {"type": "id_passport", "file": SimpleUploadedFile("x.exe", b"MZ", content_type="application/octet-stream")},
                               headers=self._h(token))
        self.assertEqual(bad.status_code, 422)
        with override_settings(SELLER_DOCUMENT_MAX_BYTES=5):
            big = self.client.post(f"{SELLER}/application/documents",
                                   {"type": "id_passport", "file": SimpleUploadedFile("id.pdf", PDF, content_type="application/pdf")},
                                   headers=self._h(token))
            self.assertEqual(big.status_code, 422)
        v1 = self.upload(token, "id_passport", "id-v1.pdf")
        v2 = self.upload(token, "id_passport", "id-v2.pdf")
        self.assertEqual((v1["version"], v2["version"]), (1, 2))
        docs = self.get(f"{SELLER}/application/documents", token).json()
        self.assertEqual(len(docs), 1)
        self.assertEqual(docs[0]["file_name"], "id-v2.pdf")
        self.assertTrue(docs[0]["url"].endswith("id-v2.pdf"))
        old = SellerDocument.objects.get(pk=v1["id"])
        self.assertEqual(str(old.superseded_by_id), v2["id"])

    def test_submit_requires_complete_application(self):
        token = self.register_seller()
        res = self.post(f"{SELLER}/application/submit", {"confirm_accuracy": True, "accept_terms": True}, token)
        self.assertEqual(res.status_code, 422)
        self.assertIn("Basic Info", res.json()["detail"])
        res = self.post(f"{SELLER}/application/submit", {"confirm_accuracy": True, "accept_terms": False}, token)
        self.assertEqual(res.status_code, 422)

    def test_individual_skips_representative_and_company_doc(self):
        token = self.register_seller(email="solo@example.com", account_type="individual")
        body = self.get(f"{SELLER}/application", token).json()
        self.assertFalse(body["steps"][2]["required"])
        self.assertTrue(body["steps"][2]["complete"])
        self.fill_all(token, account_type="individual")
        body = self.get(f"{SELLER}/application", token).json()
        self.assertTrue(body["can_submit"], body["steps"])
        self.submit(token)

    def test_full_submit_flow(self):
        token = self.register_seller()
        self.fill_all(token)
        body = self.get(f"{SELLER}/application", token).json()
        self.assertTrue(body["can_submit"], body["steps"])
        status = self.submit(token)
        self.assertEqual(status["status"], "submitted")
        self.assertRegex(status["application_number"], r"^ASK-SLR-\d{6}$")
        self.assertEqual([s["state"] for s in status["timeline"]], ["current", "pending", "pending", "pending", "pending"])
        # locked after submit
        self.assertEqual(self.patch(f"{SELLER}/application/steps/basic-info", {"first_name": "X"}, token).status_code, 409)
        self.assertEqual(self.post(f"{SELLER}/application/submit", {"confirm_accuracy": True, "accept_terms": True}, token).status_code, 409)
        # activation not yet possible
        res = self.post(f"{SELLER}/store/activate", {"accept_seller_agreement": True, "confirm_info_accurate": True}, token)
        self.assertEqual(res.status_code, 409)


class ReviewTests(SellerTestCase):
    def submitted(self, email="huda@example.com"):
        token = self.register_seller(email=email)
        self.fill_all(token)
        self.submit(token)
        return token, self.app_id(token)

    def test_non_staff_cannot_review(self):
        token, app_id = self.submitted()
        self.assertEqual(self.get(ADMIN, token).status_code, 403)
        self.assertEqual(self.post(f"{ADMIN}/{app_id}/approve", {}, token).status_code, 403)

    def test_queue_lists_submitted_only(self):
        self.register_seller(email="draft@example.com")  # stays draft
        _, app_id = self.submitted()
        res = self.get(ADMIN, self.staff_token)
        self.assertEqual(res.status_code, 200, res.content)
        items = res.json()["items"]
        self.assertEqual([i["id"] for i in items], [app_id])
        self.assertEqual(items[0]["applicant"]["email"], "huda@example.com")
        self.assertEqual(self.get(ADMIN, self.staff_token, status="approved").json()["count"], 0)
        self.assertEqual(self.get(ADMIN, self.staff_token, q="huda").json()["count"], 1)

    def test_admin_sees_decrypted_identifiers(self):
        _, app_id = self.submitted()
        body = self.get(f"{ADMIN}/{app_id}", self.staff_token).json()
        self.assertEqual(body["basic_info"]["id_passport_number"], "AB1234567")
        self.assertEqual(body["banking"]["account_number"], "001234567891234")
        self.assertEqual(len(body["all_documents"]), 4)
        self.assertEqual(body["events"][-1]["to_status"], "submitted")

    def test_approve_creates_store_and_seller_activates(self):
        token, app_id = self.submitted()
        self.assertEqual(self.post(f"{ADMIN}/{app_id}/start-review", None, self.staff_token).json()["status"], "under_review")
        res = self.post(f"{ADMIN}/{app_id}/approve", {"note": "All good"}, self.staff_token)
        self.assertEqual(res.status_code, 200, res.content)
        self.assertEqual(res.json()["status"], "approved")
        self.assertEqual(res.json()["store"]["status"], "pending_activation")
        self.assertEqual(res.json()["store"]["slug"], "huda-beauty")

        status = self.get(f"{SELLER}/application/status", token).json()
        self.assertEqual([s["state"] for s in status["timeline"]], ["done", "done", "skipped", "current", "pending"])

        # slug check
        res = self.get(f"{SELLER}/store/slug-available", token, slug="Huda Beauty")
        self.assertTrue(res.json()["available"])  # own store excluded
        other_app = SellerApplication.objects.create(user=self.staff, account_type="individual", country_id=settings.DEFAULT_COUNTRY)
        Store.objects.create(owner=self.staff, application=other_app, country_id=settings.DEFAULT_COUNTRY, display_name="Other", slug="taken")
        res = self.get(f"{SELLER}/store/slug-available", token, slug="taken")
        self.assertEqual((res.json()["available"], res.json()["suggestion"]), (False, "taken-2"))

        res = self.post(f"{SELLER}/store/activate", {"accept_seller_agreement": True, "confirm_info_accurate": False}, token)
        self.assertEqual(res.status_code, 422)
        res = self.post(f"{SELLER}/store/activate", {"slug": "taken", "accept_seller_agreement": True, "confirm_info_accurate": True}, token)
        self.assertEqual(res.status_code, 409)
        res = self.post(f"{SELLER}/store/activate", {"accept_seller_agreement": True, "confirm_info_accurate": True}, token)
        self.assertEqual(res.status_code, 200, res.content)
        self.assertEqual(res.json()["status"], "active")
        self.assertEqual(res.json()["url"], "asknget.com/store/huda-beauty")

        status = self.get(f"{SELLER}/application/status", token).json()
        self.assertEqual(status["status"], "active")
        self.assertEqual([s["state"] for s in status["timeline"]], ["done", "done", "skipped", "done", "done"])
        self.assertEqual(self.get(f"{SELLER}/store", token).json()["status"], "active")

    def test_reject(self):
        token, app_id = self.submitted()
        res = self.post(f"{ADMIN}/{app_id}/reject", {"reason": "Documents illegible"}, self.staff_token)
        self.assertEqual(res.json()["status"], "rejected")
        status = self.get(f"{SELLER}/application/status", token).json()
        self.assertEqual(status["rejection_reason"], "Documents illegible")
        self.assertEqual(status["timeline"][-1]["stage"], "Rejected")
        self.assertEqual(self.post(f"{ADMIN}/{app_id}/approve", {}, self.staff_token).status_code, 409)

    def test_more_info_round_trip(self):
        token, app_id = self.submitted()
        bad = self.post(f"{ADMIN}/{app_id}/request-info", {"message": "Fix", "items": [{"kind": "document", "target": "nope", "instruction": "x"}]}, self.staff_token)
        self.assertEqual(bad.status_code, 422)
        res = self.post(f"{ADMIN}/{app_id}/request-info", {
            "message": "Please upload a clearer copy of your company registration document and update your business address.",
            "items": [
                {"kind": "document", "target": "company_registration", "instruction": "Replace Document"},
                {"kind": "field_group", "target": "business_address", "instruction": "Your business address needs to be updated"},
            ],
        }, self.staff_token)
        self.assertEqual(res.status_code, 200, res.content)
        self.assertEqual(res.json()["status"], "more_info_required")
        self.assertEqual(self.post(f"{ADMIN}/{app_id}/request-info", {"message": "again", "items": [{"kind": "document", "target": "id_passport", "instruction": "x"}]}, self.staff_token).status_code, 409)

        # seller sees the request and the document flagged
        body = self.get(f"{SELLER}/application", token).json()
        self.assertTrue(body["editable"])
        self.assertEqual(len(body["open_info_request"]["items"]), 2)
        self.assertFalse(any(i["resolved"] for i in body["open_info_request"]["items"]))
        company = next(d for d in body["documents"] if d["type"] == "company_registration")
        self.assertEqual(company["status"], "replacement_requested")
        self.assertEqual(company["reviewer_note"], "Replace Document")

        # cannot resubmit until every item is resolved
        res = self.post(f"{SELLER}/application/resubmit", {"confirm_accuracy": True}, token)
        self.assertEqual(res.status_code, 422)
        self.upload(token, "company_registration", "company-registration-v2.pdf")
        res = self.post(f"{SELLER}/application/resubmit", {"confirm_accuracy": True}, token)
        self.assertEqual(res.status_code, 422)
        self.assertIn("business address", res.json()["detail"])
        self.patch(f"{SELLER}/application/steps/business-info", {"business_address": "26 Market Street"}, token)
        body = self.get(f"{SELLER}/application", token).json()
        self.assertTrue(all(i["resolved"] for i in body["open_info_request"]["items"]))

        res = self.post(f"{SELLER}/application/resubmit", {"confirm_accuracy": True}, token)
        self.assertEqual(res.status_code, 200, res.content)
        self.assertEqual(res.json()["status"], "resubmitted")
        self.assertIsNone(res.json()["open_info_request"])
        self.assertEqual([s["state"] for s in res.json()["timeline"]], ["done", "skipped", "current", "pending", "pending"])
        # locked again
        self.assertEqual(self.patch(f"{SELLER}/application/steps/basic-info", {"first_name": "X"}, token).status_code, 409)
        # admin can approve a resubmission; the More Info stage stays "done"
        self.post(f"{ADMIN}/{app_id}/approve", {}, self.staff_token)
        status = self.get(f"{SELLER}/application/status", token).json()
        self.assertEqual([s["state"] for s in status["timeline"]], ["done", "skipped", "done", "current", "pending"])

    def test_document_review(self):
        token, app_id = self.submitted()
        doc_id = self.get(f"{SELLER}/application/documents", token).json()[0]["id"]
        res = self.post(f"{ADMIN}/{app_id}/documents/{doc_id}/review", {"status": "accepted", "note": "Clear"}, self.staff_token)
        self.assertEqual(res.status_code, 200, res.content)
        self.assertEqual(res.json()["status"], "accepted")


class LookupTests(SellerTestCase):
    def test_lookups_are_public(self):
        res = self.client.get("/api/v1/lookups/seller")
        self.assertEqual(res.status_code, 200)
        body = res.json()
        self.assertEqual([c["value"] for c in body["account_types"]], ["individual", "sole_proprietor", "registered_company"])
        self.assertIn("business_address", [c["value"] for c in body["field_groups"]])
        self.assertEqual(body["document_max_bytes"], 10 * 1024 * 1024)

    def test_banks_filter_by_country(self):
        Bank.objects.create(name="FNB", country="ZA")
        self.assertEqual(len(self.client.get("/api/v1/lookups/banks").json()), 2)
        self.assertEqual([b["name"] for b in self.client.get("/api/v1/lookups/banks?country=za").json()], ["FNB"])

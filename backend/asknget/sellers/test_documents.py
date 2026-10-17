"""Sprint 9 phase 4: admins decide which documents sellers upload, per market and account type, from a catalogue they extend."""

from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile

from markets.models import Country, DocumentRequirement, DocumentType
from sellers.tests import PDF, SELLER, SellerTestCase

COMPANY_DOCS = ["id_passport", "company_registration", "bank_confirmation", "proof_of_address"]


class DocumentCatalogueTests(SellerTestCase):
    def setUp(self):
        super().setUp()
        self.za = Country.objects.get(code="ZA")
        self.migrated = list(DocumentRequirement.objects.filter(market=self.za).values_list("account_type", "document_type_id"))
        DocumentRequirement.objects.filter(market=self.za).delete()          # the migration's rows: this test sets its own
        self.clearance = DocumentType.objects.create(code="tax_clearance", name={"en": "Tax Clearance Certificate"}, help={"en": "Issued by SARS"},
                                                     accepted_types=["application/pdf"], max_mb=1, default_step=3)
        for order, code in enumerate(COMPANY_DOCS):
            DocumentRequirement.objects.create(market=self.za, account_type="registered_company", document_type_id=code, sort_order=order)
        DocumentRequirement.objects.create(market=self.za, account_type="registered_company", document_type=self.clearance, sort_order=9,
                                           instructions={"en": "Not older than 3 months"})
        DocumentRequirement.objects.create(market=self.za, account_type="registered_company", document_type_id="business_licence", required=False)

    def send(self, token, doc_type, name, content, mime):
        return self.client.post(f"{SELLER}/application/documents", {"type": doc_type, "file": SimpleUploadedFile(name, content, content_type=mime)},
                                headers=self._h(token))

    def application(self, token):
        return self.get(f"{SELLER}/application", token).json()

    def test_the_seed_and_the_migration_give_the_seven_built_in_types(self):
        if self.migrated:                                                   # the default market had a KYC policy: its JSON became rows
            self.assertIn(("registered_company", "company_registration"), self.migrated)
        self.assertTrue({"id_passport", "company_registration", "tax_registration", "business_licence", "bank_confirmation",
                         "proof_of_address", "other"} <= set(DocumentType.objects.values_list("code", flat=True)))

    def test_a_new_required_type_is_asked_for_with_its_own_rules_and_blocks_submitting_until_uploaded(self):
        token = self.register_seller()
        docs = self.application(token)["policy"]["required_documents"]
        self.assertEqual([(d["type"], d["required"]) for d in docs],
                         [*((c, True) for c in COMPANY_DOCS), ("tax_clearance", True), ("business_licence", False)])
        clearance = docs[4]
        self.assertEqual((clearance["label"], clearance["help"], clearance["instructions"], clearance["accepted_types"], clearance["max_mb"], clearance["step"]),
                         ("Tax Clearance Certificate", "Issued by SARS", "Not older than 3 months", ["application/pdf"], 1, 3))
        self.fill_all(token)
        for code in COMPANY_DOCS:
            self.upload(token, code)
        body = self.application(token)
        self.assertFalse(body["can_submit"])
        self.assertEqual(next(s for s in body["steps"] if s["step"] == 3)["missing_documents"], ["tax_clearance"])
        self.assertEqual(self.send(token, "tax_clearance", "c.png", b"\x89PNG", "image/png").status_code, 422)          # PDF only
        too_big = self.send(token, "tax_clearance", "c.pdf", PDF + b"0" * (1024 * 1024), "application/pdf")
        self.assertEqual((too_big.status_code, too_big.json()["detail"]), (422, "Tax Clearance Certificate must be 1 MB or smaller"))
        self.assertEqual(self.send(token, "tax_clearance", "c.pdf", PDF, "application/pdf").status_code, 201)
        self.assertTrue(self.application(token)["can_submit"])                     # the optional licence is not needed

    def test_a_switched_off_type_is_no_longer_asked_for_or_accepted_but_earlier_uploads_stay(self):
        token = self.register_seller()
        self.assertEqual(self.send(token, "tax_clearance", "c.pdf", PDF, "application/pdf").status_code, 201)
        DocumentType.objects.filter(code="tax_clearance").update(is_active=False)
        types = [d["type"] for d in self.application(token)["policy"]["required_documents"]]
        self.assertNotIn("tax_clearance", types)
        self.assertEqual(self.send(token, "tax_clearance", "c.pdf", PDF, "application/pdf").status_code, 422)
        self.assertIn("tax_clearance", [d["type"] for d in self.get(f"{SELLER}/application/documents", token).json()])
        self.assertEqual(self.send(token, "no_such_type", "c.pdf", PDF, "application/pdf").json()["detail"], "Unknown document type: no_such_type")

    def test_account_types_and_markets_without_rows_keep_the_platform_defaults(self):
        token = self.register_seller(email="solo@example.com", account_type="individual")
        self.assertEqual([d["type"] for d in self.application(token)["policy"]["required_documents"]],
                         ["id_passport", "bank_confirmation", "proof_of_address"])
        lookups = self.client.get("/api/v1/lookups/seller", {"account_type": "registered_company"}).json()
        self.assertIn({"value": "tax_clearance", "label": "Tax Clearance Certificate"}, lookups["document_types"])

    def test_staff_can_ask_for_any_catalogue_type_in_an_info_request(self):
        token = self.register_seller()
        self.fill_all(token)
        for code in COMPANY_DOCS:
            self.upload(token, code)
        self.upload(token, "tax_clearance")
        self.assertEqual(self.post(f"{SELLER}/application/submit", {"confirm_accuracy": True, "accept_terms": True}, token).status_code, 200)
        app_id = self.application(token)["id"]
        base = f"/api/v1/admin/seller-applications/{app_id}"
        self.post(f"{base}/start-review", {}, self.staff_token)
        ask = lambda target: self.post(f"{base}/request-info", {"message": "One more", "items": [{"kind": "document", "target": target, "instruction": "Please upload a recent copy"}]}, self.staff_token)
        self.assertEqual(ask("not_a_type").status_code, 422)
        self.assertEqual(ask("business_licence").status_code, 200)

    def test_bad_catalogue_rows_are_refused(self):
        bad = DocumentType(code="x", name={"en": "X"}, accepted_types=["application/zip"])
        self.assertRaises(ValidationError, bad.full_clean)
        bad = DocumentType(code="y", name={"en": "Y"}, max_mb=500)
        self.assertRaises(ValidationError, bad.full_clean)

"""Tests for banners, newsletter and contact."""

import tempfile

from django.core import mail
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings
from django.utils import timezone

from core.models import SiteSetting
from core.testing import ApiTestCase, make_user

from .models import Banner, ContactMessage, NewsletterSubscriber

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16


@override_settings(MEDIA_ROOT=tempfile.mkdtemp())
class MarketingTests(ApiTestCase):
    def _banner(self, placement, title, **kw):
        return Banner.objects.create(placement=placement, title=title, image=SimpleUploadedFile("b.png", PNG, content_type="image/png"), **kw)

    def test_banners_live_only_and_by_placement(self):
        self._banner("hero", "Carry What Matters", sort_order=1)
        self._banner("top_discount", "Upgrade your everyday", badge_text="UP TO 50% OFF")
        self._banner("hero", "Old", ends_at=timezone.now() - timezone.timedelta(days=1))
        self._banner("hero", "Future", starts_at=timezone.now() + timezone.timedelta(days=1))
        self._banner("hero", "Off", is_active=False)
        self.assertEqual([b["title"] for b in self.get("/api/v1/banners").json()], ["Carry What Matters", "Upgrade your everyday"])
        body = self.get("/api/v1/banners", placement="top_discount").json()
        self.assertEqual(body[0]["badge_text"], "UP TO 50% OFF")
        self.assertTrue(body[0]["image_url"].endswith(".png"))
        self.assertEqual(self.get("/api/v1/banners", placement="nope").status_code, 422)

    def test_newsletter(self):
        self.assertEqual(self.post("/api/v1/newsletter/subscribe", {"email": "A@Example.com"}).status_code, 201)
        self.assertEqual(self.post("/api/v1/newsletter/subscribe", {"email": "a@example.com"}).status_code, 200)
        self.assertEqual(NewsletterSubscriber.objects.count(), 1)
        NewsletterSubscriber.objects.update(unsubscribed_at=timezone.now())
        self.assertEqual(self.post("/api/v1/newsletter/subscribe", {"email": "a@example.com"}).status_code, 201)
        self.assertEqual(self.post("/api/v1/newsletter/subscribe", {"email": "nope"}).status_code, 422)

    def test_contact_guest_and_member(self):
        SiteSetting.objects.create(key="support_email", value="help@asknget.com")
        res = self.post("/api/v1/contact", {"first_name": "John", "last_name": "Ali", "email": "john.ali@gmail.com",
                                            "phone": "+92 300 1234567", "subject": "order_issue", "message": "Where is my order?"})
        self.assertEqual(res.status_code, 201, res.content)
        msg = ContactMessage.objects.get()
        self.assertEqual((msg.phone, msg.status, msg.user), ("+923001234567", "new", None))
        self.assertEqual(mail.outbox[0].to, ["help@asknget.com"])
        bad = self.post("/api/v1/contact", {"first_name": "J", "last_name": "A", "email": "x@y.com", "subject": "bogus", "message": "m"})
        self.assertEqual(bad.status_code, 422)

        user = make_user()
        token = self.token_for(user)
        res = self.post("/api/v1/contact", {"first_name": "J", "last_name": "A", "email": user.email, "message": "hi"}, token)
        self.assertEqual(ContactMessage.objects.get(pk=res.json()["id"]).user_id, user.id)

    def test_site_info(self):
        SiteSetting.objects.create(key="contact_phone", value="+92 300 1234567")
        body = self.get("/api/v1/site-info").json()
        self.assertEqual(body["contact_phone"], "+92 300 1234567")
        self.assertIn({"value": "seller_support", "label": "Seller support"}, body["contact_subjects"])

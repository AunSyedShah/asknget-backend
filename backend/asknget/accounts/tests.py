"""End-to-end tests for /api/v1/auth and /api/v1/me through the real URLconf."""

import re

from django.core import mail
from django.test import TestCase, override_settings

from .models import Address, OtpVerification, PaymentMethod, User

AUTH = "/api/v1/auth"
ME = "/api/v1/me"


class ApiTestCase(TestCase):
    def post(self, url, data=None, token=None, **kw):
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        return self.client.post(url, data=data or {}, content_type="application/json", headers=headers, **kw)

    def patch(self, url, data, token):
        return self.client.patch(url, data=data, content_type="application/json", headers={"Authorization": f"Bearer {token}"})

    def get(self, url, token):
        return self.client.get(url, headers={"Authorization": f"Bearer {token}"})

    def delete(self, url, token):
        return self.client.delete(url, headers={"Authorization": f"Bearer {token}"})

    def register(self, email="john@example.com", **extra):
        payload = {"email": email, "password": "Str0ngPassw0rd!", "confirm_password": "Str0ngPassw0rd!", **extra}
        res = self.post(f"{AUTH}/register", payload)
        self.assertEqual(res.status_code, 201, res.content)
        return res.json()


class RegistrationTests(ApiTestCase):
    def test_register_returns_user_and_tokens(self):
        body = self.register(full_name="John Ali", phone="+92 300 1234567")
        self.assertEqual(body["user"]["email"], "john@example.com")
        self.assertEqual(body["user"]["phone"], "+923001234567")  # normalised
        self.assertFalse(body["user"]["email_verified"])
        self.assertIn("access_token", body["tokens"])
        self.assertEqual(body["tokens"]["token_type"], "bearer")

    def test_email_is_normalised_and_unique(self):
        self.register(email="John@Example.com")
        res = self.post(f"{AUTH}/register", {"email": "john@example.com", "password": "Str0ngPassw0rd!", "confirm_password": "Str0ngPassw0rd!"})
        self.assertEqual(res.status_code, 409)

    def test_password_mismatch_is_422_with_field_errors(self):
        res = self.post(f"{AUTH}/register", {"email": "a@b.com", "password": "Str0ngPassw0rd!", "confirm_password": "nope"})
        self.assertEqual(res.status_code, 422)
        self.assertIn("Passwords do not match", res.json()["detail"])

    def test_weak_password_rejected(self):
        res = self.post(f"{AUTH}/register", {"email": "a@b.com", "password": "12345678", "confirm_password": "12345678"})
        self.assertEqual(res.status_code, 422)

    def test_bad_phone_rejected(self):
        res = self.post(f"{AUTH}/register", {"email": "a@b.com", "password": "Str0ngPassw0rd!", "confirm_password": "Str0ngPassw0rd!", "phone": "0300123"})
        self.assertEqual(res.status_code, 422)
        self.assertEqual(res.json()["errors"][0]["field"], "phone")

    def test_seller_register_creates_draft_application(self):
        res = self.post(
            f"{AUTH}/seller/register",
            {
                "email": "seller@example.com", "phone": "+923001111111",
                "password": "Str0ngPassw0rd!", "confirm_password": "Str0ngPassw0rd!",
                "account_type": "registered_company", "accept_terms": True,
            },
        )
        self.assertEqual(res.status_code, 201, res.content)
        user = User.objects.get(email="seller@example.com")
        self.assertTrue(user.is_seller)
        self.assertEqual(user.seller_application.account_type, "registered_company")
        self.assertEqual(user.seller_application.status, "draft")
        self.assertEqual(user.terms_acceptances.count(), 2)

    def test_seller_register_requires_terms(self):
        res = self.post(
            f"{AUTH}/seller/register",
            {"email": "s@e.com", "phone": "+923001111111", "password": "Str0ngPassw0rd!", "confirm_password": "Str0ngPassw0rd!",
             "account_type": "individual", "accept_terms": False},
        )
        self.assertEqual(res.status_code, 422)


class LoginAndTokenTests(ApiTestCase):
    def setUp(self):
        self.tokens = self.register()["tokens"]

    def test_login_ok_and_wrong_password(self):
        ok = self.post(f"{AUTH}/login", {"email": "JOHN@example.com", "password": "Str0ngPassw0rd!"})
        self.assertEqual(ok.status_code, 200)
        bad = self.post(f"{AUTH}/login", {"email": "john@example.com", "password": "wrong"})
        self.assertEqual(bad.status_code, 401)

    def test_me_requires_bearer(self):
        self.assertEqual(self.client.get(ME).status_code, 401)
        self.assertEqual(self.get(ME, self.tokens["access_token"]).status_code, 200)

    def test_refresh_rotates_and_old_token_dies(self):
        first = self.post(f"{AUTH}/refresh", {"refresh_token": self.tokens["refresh_token"]})
        self.assertEqual(first.status_code, 200)
        self.assertNotEqual(first.json()["refresh_token"], self.tokens["refresh_token"])
        replay = self.post(f"{AUTH}/refresh", {"refresh_token": self.tokens["refresh_token"]})
        self.assertEqual(replay.status_code, 401)

    def test_logout_revokes_refresh_token(self):
        res = self.post(f"{AUTH}/logout", {"refresh_token": self.tokens["refresh_token"]}, token=self.tokens["access_token"])
        self.assertEqual(res.status_code, 204)
        self.assertEqual(self.post(f"{AUTH}/refresh", {"refresh_token": self.tokens["refresh_token"]}).status_code, 401)

    def test_suspended_user_cannot_login(self):
        User.objects.filter(email="john@example.com").update(status=User.UserStatus.SUSPENDED)
        res = self.post(f"{AUTH}/login", {"email": "john@example.com", "password": "Str0ngPassw0rd!"})
        self.assertEqual(res.status_code, 403)


@override_settings(OTP_RESEND_COOLDOWN_SECONDS=45, OTP_MAX_ATTEMPTS=3)
class OtpTests(ApiTestCase):
    def setUp(self):
        body = self.register(phone="+923001234567")
        self.token = body["tokens"]["access_token"]

    def _code_from_mail(self):
        return re.search(r"\b(\d{6})\b", mail.outbox[-1].body).group(1)

    def test_email_otp_verifies_user(self):
        res = self.post(f"{AUTH}/otp/send", {"channel": "email", "purpose": "signup_email"}, token=self.token)
        self.assertEqual(res.status_code, 200, res.content)
        self.assertEqual(res.json()["target_masked"], "j***@example.com")
        code = self._code_from_mail()

        bad = self.post(f"{AUTH}/otp/verify", {"channel": "email", "purpose": "signup_email", "target": "john@example.com", "code": "000000"})
        self.assertEqual(bad.status_code, 400)

        ok = self.post(f"{AUTH}/otp/verify", {"channel": "email", "purpose": "signup_email", "target": "john@example.com", "code": code})
        self.assertEqual(ok.status_code, 200, ok.content)
        self.assertTrue(User.objects.get(email="john@example.com").email_verified)

    def test_resend_cooldown(self):
        self.post(f"{AUTH}/otp/send", {"channel": "email", "purpose": "signup_email"}, token=self.token)
        again = self.post(f"{AUTH}/otp/send", {"channel": "email", "purpose": "signup_email"}, token=self.token)
        self.assertEqual(again.status_code, 429)

    def test_max_attempts_lockout(self):
        self.post(f"{AUTH}/otp/send", {"channel": "email", "purpose": "signup_email"}, token=self.token)
        for _ in range(3):
            self.post(f"{AUTH}/otp/verify", {"channel": "email", "purpose": "signup_email", "target": "john@example.com", "code": "111111"})
        res = self.post(f"{AUTH}/otp/verify", {"channel": "email", "purpose": "signup_email", "target": "john@example.com", "code": self._code_from_mail()})
        self.assertEqual(res.status_code, 429)

    def test_code_must_be_six_digits(self):
        res = self.post(f"{AUTH}/otp/verify", {"channel": "email", "purpose": "signup_email", "target": "john@example.com", "code": "12ab"})
        self.assertEqual(res.status_code, 422)

    def test_unauthenticated_send_needs_target(self):
        res = self.post(f"{AUTH}/otp/send", {"channel": "sms", "purpose": "signup_phone"})
        self.assertEqual(res.status_code, 422)

    def test_password_reset_flow(self):
        self.assertEqual(self.post(f"{AUTH}/password/forgot", {"email": "john@example.com"}).status_code, 200)
        # unknown email: same 200, no mail sent
        self.assertEqual(self.post(f"{AUTH}/password/forgot", {"email": "ghost@example.com"}).status_code, 200)
        self.assertEqual(len(mail.outbox), 1)
        code = self._code_from_mail()
        res = self.post(f"{AUTH}/password/reset", {"email": "john@example.com", "code": code, "new_password": "An0therStrong!", "confirm_password": "An0therStrong!"})
        self.assertEqual(res.status_code, 200, res.content)
        self.assertEqual(self.post(f"{AUTH}/login", {"email": "john@example.com", "password": "An0therStrong!"}).status_code, 200)


class ProfileTests(ApiTestCase):
    def setUp(self):
        self.token = self.register()["tokens"]["access_token"]

    def test_patch_profile_partial(self):
        res = self.patch(ME, {"full_name": "John Ali", "gender": "male", "country": "pk", "date_of_birth": "1999-05-15"}, self.token)
        self.assertEqual(res.status_code, 200, res.content)
        body = res.json()
        self.assertEqual(body["country"], "PK")
        self.assertEqual(body["gender"], "male")
        self.assertIn("member_since", body)
        # a second patch without full_name must not clear it
        res = self.patch(ME, {"city": "Lahore"}, self.token)
        self.assertEqual(res.json()["full_name"], "John Ali")

    def test_invalid_enum_and_future_dob(self):
        self.assertEqual(self.patch(ME, {"gender": "unicorn"}, self.token).status_code, 422)
        self.assertEqual(self.patch(ME, {"date_of_birth": "2999-01-01"}, self.token).status_code, 422)

    def test_change_password(self):
        res = self.post(f"{ME}/password", {"current_password": "Str0ngPassw0rd!", "new_password": "N3wStrongPass!", "confirm_password": "N3wStrongPass!"}, token=self.token)
        self.assertEqual(res.status_code, 200, res.content)
        self.assertEqual(self.post(f"{AUTH}/login", {"email": "john@example.com", "password": "N3wStrongPass!"}).status_code, 200)
        wrong = self.post(f"{ME}/password", {"current_password": "nope", "new_password": "N3wStrongPass2!", "confirm_password": "N3wStrongPass2!"}, token=self.token)
        self.assertEqual(wrong.status_code, 400)


class AddressTests(ApiTestCase):
    def setUp(self):
        self.token = self.register()["tokens"]["access_token"]
        self.base = {"full_name": "John Ali", "phone": "+923001234567", "street_address": "24 Garden Avenue", "city": "Lahore", "postal_code": "54003"}

    def test_first_address_becomes_default_and_default_switches(self):
        a = self.post(f"{ME}/addresses", self.base, token=self.token).json()
        self.assertTrue(a["is_default"])
        b = self.post(f"{ME}/addresses", {**self.base, "type": "office", "label": "Office", "is_default": True}, token=self.token).json()
        self.assertTrue(b["is_default"])
        listing = self.get(f"{ME}/addresses", self.token).json()
        defaults = [x["id"] for x in listing if x["is_default"]]
        self.assertEqual(defaults, [b["id"]])

    def test_delete_default_promotes_another(self):
        a = self.post(f"{ME}/addresses", self.base, token=self.token).json()
        b = self.post(f"{ME}/addresses", {**self.base, "street_address": "Other"}, token=self.token).json()
        self.assertEqual(self.delete(f"{ME}/addresses/{a['id']}", self.token).status_code, 204)
        self.assertTrue(Address.objects.get(pk=b["id"]).is_default)
        self.assertIsNotNone(Address.all_objects.get(pk=a["id"]).deleted_at)

    def test_cannot_touch_someone_elses_address(self):
        other = self.register(email="other@example.com")["tokens"]["access_token"]
        a = self.post(f"{ME}/addresses", self.base, token=other).json()
        self.assertEqual(self.get(f"{ME}/addresses/{a['id']}", self.token).status_code, 404)

    def test_validation(self):
        res = self.post(f"{ME}/addresses", {**self.base, "type": "castle"}, token=self.token)
        self.assertEqual(res.status_code, 422)
        res = self.post(f"{ME}/addresses", {**self.base, "postal_code": ""}, token=self.token)
        self.assertEqual(res.status_code, 422)


class PaymentMethodTests(ApiTestCase):
    def setUp(self):
        self.token = self.register()["tokens"]["access_token"]
        self.card = {"type": "card", "provider": "stripe", "provider_token": "pm_123", "card_brand": "visa",
                     "card_holder_name": "John Ali", "last4": "2389", "exp_month": 8, "exp_year": 2030}

    def test_create_card_and_display_name(self):
        res = self.post(f"{ME}/payment-methods", self.card, token=self.token)
        self.assertEqual(res.status_code, 201, res.content)
        self.assertEqual(res.json()["display_name"], "VISA ....2389")
        self.assertTrue(res.json()["is_default"])

    def test_card_requires_card_fields_and_not_expired(self):
        res = self.post(f"{ME}/payment-methods", {"type": "card", "provider": "stripe", "provider_token": "pm_1"}, token=self.token)
        self.assertEqual(res.status_code, 422)
        self.assertIn("require", res.json()["detail"])
        res = self.post(f"{ME}/payment-methods", {**self.card, "exp_year": 2020}, token=self.token)
        self.assertEqual(res.status_code, 422)

    def test_paypal_needs_no_card_fields(self):
        res = self.post(f"{ME}/payment-methods", {"type": "paypal", "provider": "paypal", "provider_token": "BA-1"}, token=self.token)
        self.assertEqual(res.status_code, 201, res.content)

    def test_raw_card_number_is_not_accepted(self):
        # There is no card_number field — extra keys are ignored, never stored.
        res = self.post(f"{ME}/payment-methods", {**self.card, "card_number": "4111111111111111", "cvv": "123"}, token=self.token)
        self.assertEqual(res.status_code, 201)
        self.assertFalse(hasattr(PaymentMethod.objects.get(), "card_number"))

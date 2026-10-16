"""CORS: a browser frontend on an allowed origin may send every custom header the API reads; other origins get nothing."""

from django.test import TestCase, override_settings

ORIGIN = "http://localhost:3000"
HEADERS = "authorization,content-type,x-region,x-language,x-timezone,x-store,x-cart-token"


def preflight(client, path, origin=ORIGIN, headers=HEADERS):
    return client.options(path, headers={"Origin": origin, "Access-Control-Request-Method": "GET", "Access-Control-Request-Headers": headers})


@override_settings(CORS_ALLOWED_ORIGINS=[ORIGIN], CORS_ALLOWED_ORIGIN_REGEXES=[], CORS_ALLOW_ALL_ORIGINS=False)
class CorsTests(TestCase):
    def test_an_allowed_origin_may_send_every_custom_header_the_api_reads(self):
        res = preflight(self.client, "/api/v1/seller/products")
        self.assertEqual(res["Access-Control-Allow-Origin"], ORIGIN)
        allowed = {h.strip() for h in res["Access-Control-Allow-Headers"].split(",")}
        self.assertTrue(set(HEADERS.split(",")) <= allowed, set(HEADERS.split(",")) - allowed)    # x-store used to be missing

    def test_the_response_headers_a_frontend_needs_are_exposed(self):
        res = self.client.get("/api/v1/regions", headers={"Origin": ORIGIN})
        exposed = {h.strip().lower() for h in res["Access-Control-Expose-Headers"].split(",")}
        self.assertTrue({"x-region", "content-language"} <= exposed)

    def test_another_origin_gets_no_permission(self):
        for origin in ("https://evil.example", "http://localhost:5173"):
            self.assertNotIn("Access-Control-Allow-Origin", preflight(self.client, "/api/v1/regions", origin))

    def test_credentials_are_never_allowed(self):
        self.assertNotIn("Access-Control-Allow-Credentials", preflight(self.client, "/api/v1/regions"))

    def test_the_django_admin_gets_no_cors_headers(self):
        self.assertNotIn("Access-Control-Allow-Origin", preflight(self.client, "/admin/login/"))

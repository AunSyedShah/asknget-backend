"""CORS: what a browser on another origin is allowed to send and read.

The two failure modes worth pinning are invisible in curl and only bite in a
browser: a preflight that rejects our custom request headers (X-Cart-Token,
X-Region…), and response headers the page is not allowed to read (X-Region,
Content-Language) because they are not in Access-Control-Expose-Headers.
"""

from django.test import TestCase, override_settings

API = "/api/v1/health"
SITE = "https://za.asknget.com"


@override_settings(CORS_ALLOWED_ORIGINS=[SITE], CORS_ALLOWED_ORIGIN_REGEXES=[], CORS_ALLOW_ALL_ORIGINS=False)
class CorsTests(TestCase):
    def preflight(self, origin=SITE, headers="x-cart-token,x-region", method="GET"):
        return self.client.options(
            API, HTTP_ORIGIN=origin,
            HTTP_ACCESS_CONTROL_REQUEST_METHOD=method,
            HTTP_ACCESS_CONTROL_REQUEST_HEADERS=headers,
        )

    def test_allowed_origin_gets_headers(self):
        res = self.client.get(API, HTTP_ORIGIN=SITE)
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res["Access-Control-Allow-Origin"], SITE)
        self.assertIn("origin", res["Vary"].lower())

    def test_unknown_origin_gets_nothing(self):
        res = self.client.get(API, HTTP_ORIGIN="https://evil.example.com")
        self.assertEqual(res.status_code, 200)  # the request still runs; the browser discards the response
        self.assertNotIn("Access-Control-Allow-Origin", res)

    def test_preflight_allows_our_custom_request_headers(self):
        res = self.preflight()
        self.assertEqual(res.status_code, 200)
        allowed = {h.strip().lower() for h in res["Access-Control-Allow-Headers"].split(",")}
        self.assertLessEqual({"authorization", "content-type", "x-cart-token", "x-region", "x-language", "x-timezone"}, allowed)

    def test_response_headers_the_page_must_read_are_exposed(self):
        res = self.client.get("/api/v1/regions/current", HTTP_ORIGIN=SITE)
        exposed = {h.strip().lower() for h in res["Access-Control-Expose-Headers"].split(",")}
        self.assertLessEqual({"x-region", "content-language"}, exposed)
        self.assertIn("X-Region", res)  # the header itself is still sent

    def test_credentials_are_off_so_a_hostile_page_can_only_make_anonymous_calls(self):
        res = self.client.get(API, HTTP_ORIGIN=SITE)
        self.assertNotIn("Access-Control-Allow-Credentials", res)

    def test_only_api_paths_are_cors_enabled(self):
        """CORS_URLS_REGEX keeps the headers off /admin, /static and everything else."""
        res = self.client.get("/not-the-api/", HTTP_ORIGIN=SITE)
        self.assertEqual(res.status_code, 404)
        self.assertNotIn("Access-Control-Allow-Origin", res)

    @override_settings(CORS_ALLOWED_ORIGINS=[], CORS_ALLOWED_ORIGIN_REGEXES=[r"^https://[a-z]{2}\.asknget\.com$"])
    def test_country_subdomains_via_regex(self):
        self.assertEqual(self.client.get(API, HTTP_ORIGIN="https://pk.asknget.com")["Access-Control-Allow-Origin"], "https://pk.asknget.com")
        self.assertNotIn("Access-Control-Allow-Origin", self.client.get(API, HTTP_ORIGIN="https://asknget.com.evil.io"))

    @override_settings(CORS_ALLOWED_ORIGINS=[], CORS_ALLOWED_ORIGIN_REGEXES=[])
    def test_nothing_configured_means_no_cors_at_all(self):
        self.assertNotIn("Access-Control-Allow-Origin", self.client.get(API, HTTP_ORIGIN=SITE))

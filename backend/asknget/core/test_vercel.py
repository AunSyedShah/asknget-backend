"""Running on Vercel: no SQLite, exact hosts, serverless-safe connections, tolerant paths.

Settings are evaluated at import time, so each case imports them in a subprocess
with a controlled environment rather than juggling `override_settings`.
"""

import json
import os
import subprocess
import sys
from pathlib import Path

from django.conf import settings
from django.test import Client, SimpleTestCase, override_settings

BASE_DIR = Path(settings.BASE_DIR)
PROBE = """
import json, sys
sys.path.insert(0, %r)
import dotenv
dotenv.load_dotenv = lambda *a, **k: False     # backend/.env must not leak into the case under test
from asknget import settings as s
print(json.dumps({
    "hosts": s.ALLOWED_HOSTS, "csrf": s.CSRF_TRUSTED_ORIGINS,
    "engine": s.DATABASES["default"]["ENGINE"],
    "conn_max_age": s.DATABASES["default"].get("CONN_MAX_AGE"),
    "pool": s.DATABASES["default"].get("OPTIONS", {}).get("pool", False),
    "ssc": s.DATABASES["default"].get("DISABLE_SERVER_SIDE_CURSORS", False),
    "proxy": s.SECURE_PROXY_SSL_HEADER if hasattr(s, "SECURE_PROXY_SSL_HEADER") else None,
    "missing": s.MISSING_CONFIG, "debug": s.DEBUG, "first_middleware": s.MIDDLEWARE[0],
}))
""" % str(BASE_DIR)


def load_settings(**env):
    """Import asknget.settings in a fresh interpreter whose environment is exactly the
    process's own minus every Vercel/database variable, plus `env`."""
    full = {k: v for k, v in os.environ.items()
            if not k.startswith(("VERCEL", "DATABASE_URL", "BEHIND_PROXY", "ALLOWED_HOSTS", "CSRF_TRUSTED", "DB_POOL", "SECRET_KEY", "FIELD_ENCRYPTION_KEY", "DEBUG"))}
    full.update(env)
    return subprocess.run([sys.executable, "-c", PROBE], env=full, capture_output=True, text=True, timeout=60)


class VercelSettingsTests(SimpleTestCase):
    PG = "postgres://u:p@ep-example.neon.tech/asknget"

    CONFIGURED = {"DATABASE_URL": PG, "SECRET_KEY": "s" * 50, "FIELD_ENCRYPTION_KEY": "k"}

    def test_an_unconfigured_deployment_builds_but_serves_nothing(self):
        result = load_settings(VERCEL="1")
        self.assertEqual(result.returncode, 0, result.stderr)                       # Vercel's build can read the settings
        out = json.loads(result.stdout)
        self.assertEqual(out["missing"], ["DATABASE_URL", "SECRET_KEY", "FIELD_ENCRYPTION_KEY"])
        self.assertEqual(out["engine"], "django.db.backends.dummy")                  # never a throwaway SQLite file
        self.assertEqual(out["first_middleware"], "core.middleware.MissingConfigMiddleware")

    def test_a_configured_deployment_has_nothing_missing(self):
        out = json.loads(load_settings(VERCEL="1", **self.CONFIGURED).stdout)
        self.assertEqual(out["missing"], [])
        self.assertNotEqual(out["first_middleware"], "core.middleware.MissingConfigMiddleware")
        self.assertTrue(out["engine"].endswith("postgresql"))

    def test_debug_is_off_on_vercel_unless_asked_for(self):
        self.assertFalse(json.loads(load_settings(VERCEL="1", **self.CONFIGURED).stdout)["debug"])
        self.assertTrue(json.loads(load_settings(VERCEL="1", DEBUG="1", **self.CONFIGURED).stdout)["debug"])
        self.assertTrue(json.loads(load_settings().stdout)["debug"])                # a local checkout keeps its default

    def test_sqlite_is_still_the_fallback_everywhere_else(self):
        result = load_settings()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(json.loads(result.stdout)["engine"].endswith("sqlite3"))

    def test_this_deployments_hosts_are_allowed_exactly_not_by_wildcard(self):
        result = load_settings(VERCEL="1", DATABASE_URL=self.PG, VERCEL_URL="asknget-abc123.vercel.app",
                               VERCEL_PROJECT_PRODUCTION_URL="asknget-backend.vercel.app")
        self.assertEqual(result.returncode, 0, result.stderr)
        out = json.loads(result.stdout)
        self.assertIn("asknget-abc123.vercel.app", out["hosts"])
        self.assertIn("asknget-backend.vercel.app", out["hosts"])
        self.assertNotIn(".vercel.app", out["hosts"])
        self.assertIn("https://asknget-abc123.vercel.app", out["csrf"])
        self.assertEqual(out["proxy"], ["HTTP_X_FORWARDED_PROTO", "https"])   # TLS ends at Vercel

    def test_hosts_from_vercel_are_ignored_off_vercel(self):
        result = load_settings(VERCEL_URL="evil.example")   # no VERCEL=1
        self.assertNotIn("evil.example", json.loads(result.stdout)["hosts"])

    def test_connections_are_serverless_safe(self):
        out = json.loads(load_settings(VERCEL="1", DATABASE_URL=self.PG).stdout)
        self.assertTrue(out["engine"].endswith("postgresql"))
        self.assertEqual(out["conn_max_age"], 0)        # nothing held open between invocations
        self.assertFalse(out["pool"])                   # no per-instance pool against the DB's connection limit
        self.assertTrue(out["ssc"])                     # works behind a transaction pooler

    def test_docker_keeps_its_pool(self):
        out = json.loads(load_settings(DATABASE_URL=self.PG).stdout)
        self.assertTrue(out["pool"])
        self.assertFalse(out["ssc"])


class LeadingSlashTests(SimpleTestCase):
    """`http://host//api/v1/x` (a base URL ending in `/` joined to a path starting with `/`)."""

    def get(self, path, **extra):
        # the test client would parse '//x' as a hostname, so set PATH_INFO directly
        return Client(raise_request_exception=False).get("/api/v1/health", PATH_INFO=path, **extra)

    databases = {"default"}

    def test_a_double_slash_is_served(self):
        self.assertEqual(self.get("//api/v1/health").status_code, 200)
        self.assertEqual(self.get("////api/v1/health").status_code, 200)

    def test_a_normal_path_is_unchanged(self):
        self.assertEqual(self.get("/api/v1/health").status_code, 200)

    def test_cors_still_applies_to_the_repaired_path(self):
        # CORS and the region middleware match on /api/, so the path must be fixed before them
        res = self.get("//api/v1/health", HTTP_ORIGIN="http://localhost:3000")
        self.assertEqual(res.headers.get("Access-Control-Allow-Origin"), "http://localhost:3000")
        self.assertEqual(res.headers.get("X-Region"), settings.DEFAULT_COUNTRY)

    def test_unknown_paths_still_404(self):
        self.assertEqual(self.get("//api/v1/nope").status_code, 404)


class MissingConfigMiddlewareTests(SimpleTestCase):
    @override_settings(MISSING_CONFIG=["DATABASE_URL", "SECRET_KEY"])
    def test_every_request_gets_503_naming_what_is_missing(self):
        from django.test import RequestFactory

        from core.middleware import MissingConfigMiddleware

        response = MissingConfigMiddleware(lambda request: None)(RequestFactory().get("/api/v1/products"))
        self.assertEqual(response.status_code, 503)
        self.assertEqual(json.loads(response.content)["missing"], ["DATABASE_URL", "SECRET_KEY"])

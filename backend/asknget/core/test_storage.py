"""Where uploads live: filesystem, database (Vercel) or S3 (AWS), chosen by FILE_STORAGE; the code that handles files never knows."""

from django.core.exceptions import ImproperlyConfigured
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage, storages
from django.http import Http404
from django.test import RequestFactory, SimpleTestCase, TestCase, override_settings

from core.models import StoredFile
from core.storage import DatabaseStorage, media_from_db, private_storage
from core.storage_config import build_storages, file_storage_name
from sellers.tests import PDF, SELLER, SellerTestCase

DB_STORAGES = {
    "default": {"BACKEND": "core.storage.DatabaseStorage"},
    "private": {"BACKEND": "core.storage.DatabaseStorage"},
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
}


class ConfigTests(SimpleTestCase):
    def test_the_default_depends_on_where_it_runs_and_an_env_var_overrides_it(self):
        self.assertEqual(file_storage_name({}, on_vercel=False), "filesystem")
        self.assertEqual(file_storage_name({}, on_vercel=True), "database")
        self.assertEqual(file_storage_name({"FILE_STORAGE": " S3 "}, on_vercel=True), "s3")
        self.assertRaises(ImproperlyConfigured, file_storage_name, {"FILE_STORAGE": "ftp"}, False)

    def test_filesystem_and_database_use_one_backend_for_public_and_private_files(self):
        for name, backend in (("filesystem", "core.storage.UrlFriendlyStorage"), ("database", "core.storage.DatabaseStorage")):
            built = build_storages({"FILE_STORAGE": name}, on_vercel=False)
            self.assertEqual((built["default"]["BACKEND"], built["private"]["BACKEND"]), (backend, backend))
            self.assertIn("staticfiles", built)

    def test_s3_needs_a_bucket_and_gets_a_separate_private_one_with_expiring_links(self):
        try:
            import boto3  # noqa: F401
            import storages  # noqa: F401, F811
        except ImportError:
            self.assertRaises(ImproperlyConfigured, build_storages, {"FILE_STORAGE": "s3", "AWS_STORAGE_BUCKET_NAME": "pub"}, False)   # package missing: plain message
            return
        self.assertRaises(ImproperlyConfigured, build_storages, {"FILE_STORAGE": "s3"}, False)
        env = {"FILE_STORAGE": "s3", "AWS_STORAGE_BUCKET_NAME": "pub", "AWS_PRIVATE_BUCKET_NAME": "priv", "AWS_S3_REGION_NAME": "eu-west-1",
               "AWS_S3_CUSTOM_DOMAIN": "media.asknget.test"}
        built = build_storages(env, False, signed_ttl=600)
        self.assertEqual((built["default"]["OPTIONS"]["bucket_name"], built["default"]["OPTIONS"]["custom_domain"], built["default"]["OPTIONS"]["querystring_auth"]),
                         ("pub", "media.asknget.test", False))
        self.assertEqual((built["private"]["OPTIONS"]["bucket_name"], built["private"]["OPTIONS"]["querystring_auth"], built["private"]["OPTIONS"]["querystring_expire"]),
                         ("priv", True, 600))


@override_settings(STORAGES=DB_STORAGES)
class DatabaseStorageTests(TestCase):
    def test_a_file_round_trips_through_the_database(self):
        name = default_storage.save("products/a.png", ContentFile(b"\x89PNG-bytes"))
        self.assertEqual(name, "products/a.png")
        self.assertTrue(default_storage.exists(name))
        self.assertEqual((default_storage.size(name), default_storage.open(name).read()), (10, b"\x89PNG-bytes"))
        self.assertEqual(default_storage.url(name), "/media/products/a.png")
        self.assertEqual(StoredFile.objects.get(name=name).content_type, "image/png")
        default_storage.delete(name)
        self.assertFalse(default_storage.exists(name))

    def test_a_second_file_with_the_same_name_gets_its_own_name_and_neither_is_overwritten(self):
        first = default_storage.save("products/same.png", ContentFile(b"one"))
        second = default_storage.save("products/same.png", ContentFile(b"two"))
        self.assertNotEqual(first, second)
        self.assertEqual((default_storage.open(first).read(), default_storage.open(second).read()), (b"one", b"two"))

    def test_a_missing_file_is_a_file_not_found_and_a_web_address_is_returned_as_it_is(self):
        self.assertRaises(FileNotFoundError, default_storage.open, "nope.png")
        self.assertEqual(default_storage.url("https://picsum.photos/seed/a/10/10"), "https://picsum.photos/seed/a/10/10")

    def test_dangerous_names_are_refused(self):
        for bad in ("../x.png", "/etc/x.png"):
            self.assertRaises(Exception, default_storage.save, bad, ContentFile(b"x"))

    @override_settings(DB_STORAGE_MAX_BYTES=10)
    def test_a_file_over_the_limit_is_refused_not_half_stored(self):
        self.assertRaises(ValueError, default_storage.save, "products/big.png", ContentFile(b"x" * 11))
        self.assertFalse(StoredFile.objects.exists())

    def test_the_private_alias_is_the_same_backend_here(self):
        self.assertIsInstance(private_storage(), DatabaseStorage)
        self.assertIs(private_storage(), storages["private"])

    def test_the_media_view_serves_public_files_with_caching_and_never_shows_a_download_as_a_page(self):
        default_storage.save("products/a.png", ContentFile(b"\x89PNG"))
        default_storage.save("reviews/note.html", ContentFile(b"<script>alert(1)</script>"))
        rf = RequestFactory()
        ok = media_from_db(rf.get("/media/products/a.png"), "products/a.png")
        self.assertEqual((ok.status_code, ok["Content-Type"], ok["X-Content-Type-Options"], bytes(ok.content)), (200, "image/png", "nosniff", b"\x89PNG"))
        self.assertIn("max-age", ok["Cache-Control"])
        again = media_from_db(rf.get("/media/products/a.png", headers={"If-None-Match": ok["ETag"]}), "products/a.png")
        self.assertEqual(again.status_code, 304)
        page = media_from_db(rf.get("/media/reviews/note.html"), "reviews/note.html")
        self.assertEqual(page["Content-Type"], "application/octet-stream")          # not text/html
        self.assertRaises(Http404, media_from_db, rf.get("/media/nope.png"), "nope.png")

    def test_private_papers_are_never_served_from_media(self):
        default_storage.save("seller_documents/1/id_passport/x.pdf", ContentFile(b"%PDF"))
        default_storage.save("category_requests/x.png", ContentFile(b"x"))
        for name in ("seller_documents/1/id_passport/x.pdf", "category_requests/x.png"):
            self.assertRaises(Http404, media_from_db, RequestFactory().get(f"/media/{name}"), name)


@override_settings(STORAGES=DB_STORAGES)
class SellerDocumentOnDatabaseStorageTests(SellerTestCase):
    def setUp(self):
        super().setUp()
        # A field's storage is resolved once, when the model loads (as in production, where settings never change): point this one at the database.
        from sellers.models import SellerDocument

        field = SellerDocument._meta.get_field("file")
        original, field.storage = field.storage, DatabaseStorage()
        self.addCleanup(setattr, field, "storage", original)

    def test_a_seller_document_uploads_and_opens_through_its_signed_link_with_no_disk(self):
        token = self.register_seller()
        doc = self.upload(token, "id_passport")
        listed = self.get(f"{SELLER}/application/documents", token).json()
        self.assertEqual([d["type"] for d in listed], ["id_passport"])
        self.assertTrue(StoredFile.objects.filter(name__startswith="seller_documents/").exists())
        res = self.client.get(listed[0]["url"])
        self.assertEqual((res.status_code, b"".join(res.streaming_content), res["Cache-Control"]), (200, PDF, "private, no-store"))
        self.assertEqual(self.client.get(listed[0]["url"].split("?")[0] + "?t=forged").status_code, 404)
        self.assertTrue(doc)

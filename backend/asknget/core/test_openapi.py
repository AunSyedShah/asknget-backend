"""The OpenAPI document declares the headers and the error body the whole API shares (core.openapi)."""

import re

from django.test import TestCase

from asknget.api import api

PREFIX = "/api/v1"


def operations(schema):
    for path, item in schema["paths"].items():
        for method in ("get", "post", "put", "patch", "delete"):
            if method in item:
                yield path, method, item[method]


def header_names(schema, operation):
    names = []
    for param in operation.get("parameters", []):
        if "$ref" in param:
            param = schema["components"]["parameters"][param["$ref"].rsplit("/", 1)[1]]
        if param["in"] == "header":
            names.append(param["name"])
    return names


class OpenApiHeaderTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.schema = api.get_openapi_schema()

    def test_every_operation_declares_region_language_and_timezone(self):
        for path, method, op in operations(self.schema):
            names = header_names(self.schema, op)
            for header in ("X-Region", "X-Language", "X-Timezone"):
                self.assertIn(header, names, f"{method} {path}")

    def test_the_store_header_is_declared_on_seller_calls_only(self):
        for path, method, op in operations(self.schema):
            self.assertEqual("X-Store" in header_names(self.schema, op), path.startswith(f"{PREFIX}/seller/"), f"{method} {path}")

    def test_the_cart_token_is_declared_once_on_cart_and_checkout_calls_only(self):
        seen = 0
        for path, method, op in operations(self.schema):
            names = header_names(self.schema, op)
            on_cart = path.startswith(f"{PREFIX}/cart") or path.startswith(f"{PREFIX}/checkout")
            self.assertEqual(names.count("X-Cart-Token"), 1 if on_cart else 0, f"{method} {path}")
            seen += on_cart
        self.assertGreater(seen, 3)

    def test_no_header_is_declared_with_underscores_or_in_a_non_canonical_case(self):
        for path, method, op in operations(self.schema):
            for name in header_names(self.schema, op):
                self.assertIsNone(re.search(r"_|[a-z]", name[:1]), f"{method} {path}: {name}")

    def test_every_operation_has_an_error_response_with_the_real_shape(self):
        error = self.schema["components"]["schemas"]["ErrorResponse"]
        self.assertEqual(sorted(error["properties"]), ["detail", "errors"])
        for path, method, op in operations(self.schema):
            has_error = "default" in op["responses"] or any(code.startswith("4") for code in op["responses"])
            self.assertTrue(has_error, f"{method} {path}")

    def test_every_reference_resolves(self):
        def refs(node):
            if isinstance(node, dict):
                if "$ref" in node:
                    yield node["$ref"]
                for value in node.values():
                    yield from refs(value)
            elif isinstance(node, list):
                for value in node:
                    yield from refs(value)

        for ref in set(refs(self.schema)):
            target = self.schema
            for part in ref.removeprefix("#/").split("/"):
                self.assertIn(part, target, ref)
                target = target[part]

    def test_running_it_twice_changes_nothing(self):
        again = api.get_openapi_schema()
        self.assertEqual(again, self.schema)
        self.assertEqual(len(list(operations(again))), len(list(operations(self.schema))))

    def test_the_description_says_the_base_url_is_the_origin(self):
        self.assertIn("origin only", self.schema["info"]["description"])

    def test_the_document_is_served(self):
        body = self.client.get("/api/v1/openapi.json").json()
        self.assertIn("XRegion", body["components"]["parameters"])
        self.assertEqual(body["info"]["title"], "asknget API")

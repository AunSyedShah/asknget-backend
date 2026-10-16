"""No two API schemas may share a class name.

The OpenAPI document keys `components.schemas` by class name, so two different classes with one name collapse into one entry:
the server still returns the right data, but a client generated from the document gets the wrong type for one of them.
"""

import collections

from django.test import TestCase
from ninja import Schema

from asknget.api import api   # importing the root API imports every app's schemas


def project_schemas():
    seen, stack = [], [Schema]
    while stack:
        for cls in stack.pop().__subclasses__():
            stack.append(cls)
            module = cls.__module__
            if module.startswith(("ninja", "pydantic")) or module.startswith("django"):
                continue
            seen.append(cls)
    return seen


class SchemaNameTests(TestCase):
    def test_every_schema_class_has_a_unique_name(self):
        by_name = collections.defaultdict(set)
        for cls in project_schemas():
            by_name[cls.__name__].add(f"{cls.__module__}.{cls.__qualname__}")
        clashes = {name: sorted(where) for name, where in by_name.items() if len(where) > 1}
        self.assertEqual(clashes, {}, "Give each class a name no other API schema has (see core/test_schema_names.py)")

    def test_the_document_describes_the_shapes_the_server_returns(self):
        schemas = api.get_openapi_schema()["components"]["schemas"]
        fields = lambda name: set(schemas[name]["properties"])
        # cart: the delivery rule is the buyer's, the seller's rule is another thing
        self.assertTrue({"store_subtotal_cents", "remaining_cents", "free"} <= fields("CartDeliveryRuleOut"))
        self.assertTrue({"locked", "source", "own", "currency"} <= fields("DeliveryRuleOut"))
        self.assertTrue({"waived", "follows_rule"} <= fields("CartDeliveryOptionOut"))
        # catalog and chat and notifications
        self.assertTrue({"id", "name", "slug"} <= fields("CategoryRefOut"))
        self.assertTrue({"id", "name"} <= fields("RequestedParentOut"))
        self.assertTrue({"id", "display_name", "slug"} <= fields("StoreRefOut"))
        self.assertTrue({"id", "name", "values"} <= fields("OptionTypeOut"))
        self.assertTrue({"unread", "tabs"} <= fields("NotificationSummaryOut"))
        self.assertIn("detail", fields("MessageOut"))
        self.assertTrue({"body"} <= fields("ChatMessageOut"))
        # accounts vs checkout
        self.assertIn("street_address", fields("AddressIn"))
        self.assertTrue({"address", "city", "postal_code"} <= fields("CheckoutAddressIn"))
        self.assertTrue({"first_name", "phone"} <= fields("ContactIn"))
        self.assertIn("message", fields("ContactFormIn"))

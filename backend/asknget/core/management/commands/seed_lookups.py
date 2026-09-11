"""Idempotent seed for lookup tables taken straight from the Figma design.

    python manage.py seed_lookups

Safe to re-run: uses update_or_create keyed on slug/name.
"""

from django.core.management.base import BaseCommand
from django.utils.text import slugify

from catalog.models import Brand, Category
from core.models import Bank
from orders.models import DeliveryMethod

# (name, [children]) — top-level list is the union of the seller form,
# buyer filter sidebar and landing "Shop by Categories". TODO(design): confirm canonical tree.
CATEGORIES = [
    ("Fashion", ["Shoes", "Bags"]),
    ("Beauty", ["Makeup", "Skincare", "Fragrance"]),
    ("Electronics", []),
    ("Groceries", ["Food", "Chocolate"]),
    ("Jewellery", []),
    ("Sports", []),
    ("Home & Lifestyle", ["Crockery", "Furniture", "Essentials"]),
    ("Accessories", ["Mobile Accessories", "Digital Accessories"]),
    ("Other", []),
]

BRANDS = ["ZARA", "NARS", "Philips", "Luminarc", "Michael Kors", "L'Oréal Paris", "Samsung", "CHANEL", "Christian Dior", "Jimmy Choo"]

BANKS = [
    # Pakistan — design shows "Meezan Bank"
    ("PK", "Meezan Bank", "MEZNPKKA"),
    ("PK", "Habib Bank Limited", "HABBPKKA"),
    ("PK", "United Bank Limited", "UNILPKKA"),
    ("PK", "MCB Bank", "MUCBPKKA"),
    ("PK", "Allied Bank", "ABPAPKKA"),
    ("PK", "Bank Alfalah", "ALFHPKKA"),
    ("PK", "Standard Chartered Pakistan", "SCBLPKKX"),
    # South Africa — design shows Johannesburg / Gauteng addresses
    ("ZA", "Standard Bank", "SBZAZAJJ"),
    ("ZA", "First National Bank", "FIRNZAJJ"),
    ("ZA", "ABSA", "ABSAZAJJ"),
    ("ZA", "Nedbank", "NEDSZAJJ"),
    ("ZA", "Capitec", "CABLZAJJ"),
]

DELIVERY_METHODS = [
    # name, eta_min, eta_max, price_cents, sort_order
    ("Standard Delivery", 5, 7, 1099, 1),
    ("Express Delivery", 2, 3, 1999, 2),
]


class Command(BaseCommand):
    help = "Seed categories, brands, banks and delivery methods from the design."

    def handle(self, *args, **options):
        created = {"categories": 0, "brands": 0, "banks": 0, "delivery_methods": 0}

        for order, (name, children) in enumerate(CATEGORIES):
            parent, was_created = Category.objects.update_or_create(
                slug=slugify(name), defaults={"name": name, "parent": None, "sort_order": order}
            )
            created["categories"] += was_created
            for child_order, child in enumerate(children):
                _, was_created = Category.objects.update_or_create(
                    slug=slugify(child), defaults={"name": child, "parent": parent, "sort_order": child_order}
                )
                created["categories"] += was_created

        for name in BRANDS:
            _, was_created = Brand.objects.update_or_create(slug=slugify(name), defaults={"name": name})
            created["brands"] += was_created

        for country, name, swift in BANKS:
            _, was_created = Bank.objects.update_or_create(country=country, name=name, defaults={"swift": swift})
            created["banks"] += was_created

        for name, eta_min, eta_max, price, order in DELIVERY_METHODS:
            _, was_created = DeliveryMethod.objects.update_or_create(
                name=name,
                defaults={"eta_min_days": eta_min, "eta_max_days": eta_max, "price_cents": price, "sort_order": order},
            )
            created["delivery_methods"] += was_created

        for key, n in created.items():
            self.stdout.write(f"{key}: {n} created")
        self.stdout.write(self.style.SUCCESS("Lookups seeded."))

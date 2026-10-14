"""Idempotent seed for lookup tables taken straight from the Figma design.

    python manage.py seed_lookups

Safe to re-run: uses update_or_create keyed on slug/name.
"""

from django.core.management.base import BaseCommand
from django.utils.text import slugify

from catalog.models import Brand, Category
from core.models import Bank
from markets.models import Country, KycPolicy, ReturnPolicy
from orders.models import DeliveryMethod

# Markets. Each country is a separate market (own currency, timezone, languages,
# KYC + return policy). Staff edit these in admin; this is the starting point.
COUNTRIES = {
    "ZA": {
        "country": dict(name="South Africa", subdomain="za", currency="ZAR", timezone="Africa/Johannesburg",
                        default_language="en", languages=["en", "af", "zu"], phone_prefix="+27",
                        region_label="Province", postal_code_regex=r"^\d{4}$", sort_order=1),
        "kyc": dict(
            id_number_label="SA ID / Passport number",
            id_number_regex=r"^(\d{13}|[A-Z0-9]{6,9})$",
            tax_id_label="VAT number",
            company_registration_label="CIPC registration number",
            document_labels={
                "id_passport": {"en": "SA ID / Passport", "af": "SA ID / Paspoort", "zu": "I-ID yase-SA / Ipasipoti"},
                "company_registration": {"en": "CIPC company registration"},
                "bank_confirmation": {"en": "Bank confirmation letter"},
                "proof_of_address": {"en": "Proof of address (≤ 3 months)"},
            },
            instructions={"en": "Upload clear copies. We verify every application manually within 2 business days."},
        ),
        "returns": dict(window_days=30, defect_days=180, summary={"en": "Return eligible items within 30 days", "af": "Stuur geskikte items binne 30 dae terug"}),
    },
    "PK": {
        "country": dict(name="Pakistan", subdomain="pk", currency="PKR", timezone="Asia/Karachi",
                        default_language="en", languages=["en", "ur"], phone_prefix="+92",
                        region_label="Province", postal_code_regex=r"^\d{5}$", sort_order=2),
        "kyc": dict(
            id_number_label="CNIC / Passport number",
            id_number_regex=r"^(\d{5}-?\d{7}-?\d|[A-Z]{2}\d{7})$",
            tax_id_label="NTN",
            company_registration_label="SECP registration number",
            # Companies also upload their NTN certificate; individuals keep the platform defaults.
            required_documents={
                "sole_proprietor": ["id_passport", "company_registration", "tax_registration", "bank_confirmation", "proof_of_address"],
                "registered_company": ["id_passport", "company_registration", "tax_registration", "bank_confirmation", "proof_of_address"],
            },
            document_labels={
                "id_passport": {"en": "CNIC / Passport", "ur": "شناختی کارڈ / پاسپورٹ"},
                "company_registration": {"en": "SECP incorporation certificate"},
                "tax_registration": {"en": "NTN certificate (FBR)"},
                "bank_confirmation": {"en": "Bank account maintenance certificate"},
                "proof_of_address": {"en": "Proof of address (utility bill ≤ 3 months)"},
            },
            instructions={"en": "Upload clear copies. We verify every application manually within 2 business days.",
                          "ur": "واضح کاپیاں اپ لوڈ کریں۔ ہر درخواست کی تصدیق 2 کاروباری دنوں میں دستی طور پر کی جاتی ہے۔"},
        ),
        "returns": dict(window_days=7, defect_days=14, summary={"en": "Return eligible items within 7 days", "ur": "اہل اشیاء 7 دن کے اندر واپس کریں"}),
    },
}

# (name, [children]) — top-level list is the union of the seller form,
# buyer filter sidebar and landing "Shop by Categories". TODO(design): confirm canonical tree.
# Rows are matched by slug and slugs are unique across the whole tree, so a name must appear once:
# reusing one (a child "Accessories" under Fashion) would move the existing category. Append; do not rename
# (products point at these rows). core/test_seed_lookups.py guards both.
CATEGORIES = [
    ("Fashion", ["Shoes", "Bags", "Women's Clothing", "Men's Clothing", "Kids' Clothing", "Eyewear"]),
    ("Beauty", ["Makeup", "Skincare", "Fragrance", "Hair Care", "Bath & Body", "Nail Care", "Men's Grooming"]),
    ("Electronics", ["Mobile Phones", "Laptops & Computers", "Audio & Headphones", "Cameras", "TV & Home Entertainment", "Wearables"]),
    ("Groceries", ["Food", "Chocolate", "Beverages", "Snacks", "Pantry Staples"]),
    ("Jewellery", ["Watches", "Rings", "Necklaces", "Earrings"]),
    ("Sports", ["Fitness Equipment", "Activewear", "Outdoor & Camping", "Cycling"]),
    ("Home & Lifestyle", ["Crockery", "Furniture", "Essentials", "Home Decor", "Bedding & Bath", "Lighting"]),
    ("Accessories", ["Mobile Accessories", "Digital Accessories", "Belts & Wallets"]),
    ("Health & Wellness", ["Vitamins & Supplements", "Personal Care Devices", "Medical Supplies"]),
    ("Baby & Kids", ["Baby Care", "Toys & Games", "School Supplies"]),
    ("Books & Stationery", ["Books", "Stationery", "Art Supplies"]),
    ("Pet Supplies", ["Pet Food", "Pet Accessories"]),
    ("Automotive", ["Car Accessories", "Car Care"]),
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
    # name, eta_min, eta_max, price_cents (in each market's own currency), sort_order — seeded per active country
    ("Standard Delivery", 5, 7, 1099, 1),
    ("Express Delivery", 2, 3, 1999, 2),
]


class Command(BaseCommand):
    help = "Seed countries (markets), categories, brands, banks and delivery methods from the design."

    def handle(self, *args, **options):
        created = {"countries": 0, "categories": 0, "brands": 0, "banks": 0, "delivery_methods": 0}

        for code, spec in COUNTRIES.items():
            country, was_created = Country.objects.update_or_create(code=code, defaults={**spec["country"], "is_active": True})
            if was_created and not Country.objects.filter(is_default=True).exclude(pk=code).exists():
                country.is_default = True
                country.save()
            KycPolicy.objects.update_or_create(country=country, defaults=spec["kyc"])
            ReturnPolicy.objects.update_or_create(country=country, defaults=spec["returns"])
            created["countries"] += was_created

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

        for country in Country.objects.filter(is_active=True):
            for name, eta_min, eta_max, price, order in DELIVERY_METHODS:
                _, was_created = DeliveryMethod.objects.update_or_create(
                    country=country, name=name,
                    defaults={"eta_min_days": eta_min, "eta_max_days": eta_max, "price_cents": price, "sort_order": order},
                )
                created["delivery_methods"] += was_created

        for key, n in created.items():
            self.stdout.write(f"{key}: {n} created")
        self.stdout.write(self.style.SUCCESS("Lookups seeded."))

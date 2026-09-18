"""Demo data mirroring the Figma sample content, for local frontend work.

    python manage.py seed_lookups     # first: categories, brands, banks, delivery methods
    python manage.py seed_demo        # then: demo accounts, stores, products, vouchers, banners

Idempotent (keyed on emails / slugs / codes). Never run against production.

Accounts (password for all: DemoPass123!):
  admin@asknget.demo    staff — Django admin + /admin/* endpoints
  seller@asknget.demo   active seller with a store and products
  customer@asknget.demo customer with an address and an order
"""

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone
from django.utils.text import slugify

from accounts.models import Address, User
from catalog.models import Brand, Category, Product, ProductContentSection, ProductOptionType, ProductOptionValue, ProductVariant, Review
from catalog.services import refresh_category_counts, refresh_product_rating
from core.models import SiteSetting
from orders.models import Voucher
from sellers.models import SellerApplication, Store

PASSWORD = "DemoPass123!"

# (name, category slug, brand, price, compare_at, flags, options, short description)
PRODUCTS = [
    ("NARS BB Cream", "makeup", "NARS", 2599, 4099, {"is_featured": True, "is_best_seller": True},
     {"Size": ["15 ml", "30 ml", "50 ml", "75 ml"], "Shade": ["Ivory", "Natural", "Caramel", "Deep Honey"]},
     "Lightweight, buildable coverage that helps even the appearance of skin tone while creating a smooth, natural looking finish."),
    ("Christian Dior Bag", "bags", "Christian Dior", 9512, 15853, {"is_featured": True}, {"Color": ["Black", "Cream"]}, "Structured everyday bag."),
    ("Jimmy Choo Heels", "shoes", "Jimmy Choo", 12012, 14512, {"is_featured": True}, {"Size": ["36", "37", "38", "39"], "Color": ["Black", "Red"]}, "Classic pointed heels."),
    ("Black Earbuds", "mobile-accessories", "Samsung", 1299, 2099, {"is_featured": True, "is_best_seller": True}, {"Color": ["Black"]}, "Wireless earbuds with 24h battery."),
    ("Golden Teapot", "crockery", "Luminarc", 12012, 14512, {"is_new_arrival": True}, {}, "Hand-finished ceramic teapot."),
    ("LED Console", "electronics", "Philips", 7512, 8512, {"is_new_arrival": True}, {}, "Ambient LED console light."),
    ("Selfie Stick", "digital-accessories", "Samsung", 1899, 2599, {}, {"Color": ["Black", "Green"]}, "Bluetooth selfie stick with tripod."),
    ("Coco Chanel Perfume", "fragrance", "CHANEL", 9212, 9912, {"is_best_seller": True}, {"Size": ["50 ml", "100 ml"]}, "Eau de parfum."),
    ("Silver Jewellery", "jewellery", None, 4599, None, {"is_featured": True}, {}, "Sterling silver set."),
    ("Dining Table", "furniture", None, 9599, 12599, {}, {"Color": ["Gray", "Oak"]}, "Six-seater dining table."),
    ("Chocolate Balls", "chocolate", None, 7512, 9512, {"is_best_seller": True}, {}, "Assorted pralines."),
    ("White Teddy Bear", "essentials", None, 2999, None, {"is_best_seller": True}, {}, "Soft plush bear."),
    ("Apple Watch", "digital-accessories", None, 4999, 5499, {}, {"Color": ["Orange", "Black"]}, "Smart watch."),
]


class Command(BaseCommand):
    help = "Seed demo accounts, a store, products, vouchers and banners (dev only)."

    def handle(self, *args, **options):
        if not settings.DEBUG:
            raise CommandError("Refusing to seed demo data with DEBUG=False")
        if not Category.objects.exists():
            raise CommandError("Run `manage.py seed_lookups` first")

        admin, _ = User.objects.get_or_create(email="admin@asknget.demo", defaults={"full_name": "Demo Admin", "is_staff": True, "is_superuser": True})
        seller, _ = User.objects.get_or_create(email="seller@asknget.demo", defaults={"full_name": "Ahmad Khan", "is_seller": True, "phone": "+923001234567"})
        customer, _ = User.objects.get_or_create(email="customer@asknget.demo", defaults={"full_name": "John Ali", "phone": "+923007654321"})
        for u in (admin, seller, customer):
            u.set_password(PASSWORD)
            u.email_verified_at = u.email_verified_at or timezone.now()
            u.save()

        app, _ = SellerApplication.objects.get_or_create(
            user=seller,
            defaults=dict(account_type="registered_company", status="active", application_number="ASK-SLR-004821", current_step=5,
                          first_name="Ahmad", last_name="Khan", store_display_name="Huda Beauty", legal_business_name="Huda Beauty (Pty) Ltd",
                          submitted_at=timezone.now(), approved_at=timezone.now(), activated_at=timezone.now()),
        )
        store, _ = Store.objects.get_or_create(
            application=app, defaults=dict(owner=seller, display_name="Huda Beauty", slug="huda-beauty", status="active",
                                           description="Premium beauty and lifestyle products.", activated_at=timezone.now()),
        )

        Address.objects.get_or_create(
            user=customer, street_address="123 Main Street",
            defaults=dict(full_name="John Ali", phone="+923007654321", city="Lahore", postal_code="45009", country="PK", is_default=True),
        )

        brands = {b.name: b for b in Brand.objects.all()}
        created = 0
        for name, cat_slug, brand, price, compare, flags, options, blurb in PRODUCTS:
            category = Category.objects.filter(slug=cat_slug).first() or Category.objects.filter(parent__isnull=True).first()
            product, was_created = Product.objects.get_or_create(
                slug=slugify(name),
                defaults=dict(store=store, category=category, brand=brands.get(brand), name=name, price_cents=price, compare_at_price_cents=compare,
                              short_description=blurb, description_title=f"Perfecting {name}", description=blurb,
                              status="active", published_at=timezone.now(), **flags),
            )
            created += was_created
            if not was_created:
                continue
            combos = [[]]
            for i, (opt, values) in enumerate(options.items()):
                ot = ProductOptionType.objects.create(product=product, name=opt, sort_order=i)
                vals = [ProductOptionValue.objects.create(option_type=ot, value=v, sort_order=j) for j, v in enumerate(values)]
                combos = [c + [v] for c in combos for v in vals]
            for i, combo in enumerate(combos):
                variant = ProductVariant.objects.create(product=product, sku=f"{slugify(name)[:20].upper()}-{i:02d}", stock_qty=25)
                variant.option_values.set(combo)
            if name == "NARS BB Cream":
                for j, (t, b) in enumerate([
                    ("Natural Coverage", "Provides light-to-medium, buildable coverage that helps even the appearance of skin tone."),
                    ("Lightweight & Comfortable", "The smooth, lightweight texture blends easily and feels comfortable on the skin."),
                    ("Smooth Finish", "Leaves the complexion looking naturally fresh and radiant."),
                    ("Buildable Formula", "Start with a thin layer for a sheer look, then add more where you want coverage."),
                ]):
                    ProductContentSection.objects.create(product=product, section="benefit", title=t, body=b, sort_order=j)
                for j, (t, b) in enumerate([("Prep", "Apply to clean, moisturised skin."), ("Blend", "Dot on and blend outwards with fingers or a sponge."), ("Build", "Add a second layer where needed.")]):
                    ProductContentSection.objects.create(product=product, section="usage_step", title=t, body=b, sort_order=j)
                for who, rating, body in (("Ayesha Khan", 5, "Really comfortable to wear throughout the day and easy to blend."),
                                          ("Sara Mitchell", 5, "The finish is beautiful and natural."),
                                          ("Bilal Ahmed", 4, "Lightweight and incredibly easy to blend.")):
                    Review.objects.create(product=product, reviewer_name=who, reviewer_email="demo@asknget.demo", rating=rating, body=body, status="approved")
                refresh_product_rating(product)
        refresh_category_counts()
        from discovery.indexing import embed_products

        embed_products()

        Voucher.objects.get_or_create(code="SAVE20", defaults=dict(type="percent", value=20, min_subtotal_cents=5000))
        Voucher.objects.get_or_create(code="WELCOME5", defaults=dict(type="fixed", value=500, per_user_limit=1))

        for key, value in {
            "contact_phone": "+92 300 1234567",
            "contact_email": "info@asknget.com",
            "support_email": "support@asknget.com",
            "accepted_payments": ["visa", "mastercard", "paypal", "google_pay", "apple_pay"],
        }.items():
            SiteSetting.objects.get_or_create(key=key, defaults={"value": value})

        self.stdout.write(self.style.SUCCESS(f"Demo data ready ({created} products created). Password for demo accounts: {PASSWORD}"))

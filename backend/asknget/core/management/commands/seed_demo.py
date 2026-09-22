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
from datetime import timedelta

from django.db import models
from django.utils import timezone
from django.utils.text import slugify

from accounts.models import Address, User
from catalog.models import Brand, Category, Product, ProductContentSection, ProductOptionType, ProductOptionValue, ProductVariant, Review
from catalog.services import refresh_category_counts, refresh_product_rating, refresh_stock_status
from core.models import SiteSetting
from orders import services as order_services
from orders.models import Order, OrderItem, Voucher
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

        from markets import services as markets

        country = markets.default_country()
        if country is None:
            self.stderr.write("No market configured — run `manage.py seed_lookups` first.")
            return

        app, _ = SellerApplication.objects.get_or_create(
            user=seller,
            defaults=dict(account_type="registered_company", status="active", application_number="ASK-SLR-004821", current_step=5, country=country,
                          first_name="Ahmad", last_name="Khan", store_display_name="Huda Beauty", legal_business_name="Huda Beauty (Pty) Ltd",
                          submitted_at=timezone.now(), approved_at=timezone.now(), activated_at=timezone.now()),
        )
        store, _ = Store.objects.get_or_create(
            application=app, defaults=dict(owner=seller, country=country, display_name="Huda Beauty", slug="huda-beauty", status="active",
                                           description="Premium beauty and lifestyle products.", activated_at=timezone.now()),
        )

        Address.objects.get_or_create(
            user=customer, street_address="123 Main Street",
            defaults=dict(full_name="John Ali", phone="+923007654321", city="Lahore", postal_code="45009", country=country.code, is_default=True),
        )

        brands = {b.name: b for b in Brand.objects.all()}
        created = 0
        for name, cat_slug, brand, price, compare, flags, options, blurb in PRODUCTS:
            category = Category.objects.filter(slug=cat_slug).first() or Category.objects.filter(parent__isnull=True).first()
            product, was_created = Product.objects.get_or_create(
                slug=slugify(name),
                defaults=dict(store=store, country=country, currency=country.currency, category=category, brand=brands.get(brand), name=name, price_cents=price, compare_at_price_cents=compare,
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
        self._seed_second_market(brands)
        self._seed_chat(store, seller, customer)
        refresh_category_counts()
        from discovery.indexing import embed_products

        embed_products()

        # Demo data seeded before markets existed was priced in USD; a store's products are in its market's currency.
        Product.objects.filter(store=store).exclude(currency=country.currency).update(currency=country.currency)
        for product in Product.objects.filter(store=store):
            refresh_stock_status(product)
        self._seed_orders(store, seller, customer)

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

    # Seller-dashboard demo: a few months of orders from a handful of customers,
    # moved through fulfilment so KPIs, charts, timelines and customer types all
    # have something to show. Skipped when demo orders already exist.
    DEMO_CUSTOMERS = [
        ("Olivia", "Martin", "olivia.martin@example.com"),
        ("Daniel", "Peters", "daniel.peters@example.com"),
        ("Emma", "Roberts", "emma.roberts@example.com"),
        ("James", "Khan", "james.khan@example.com"),
        ("Sarah", "Lewis", "sarah.lewis@example.com"),
    ]
    # (days ago, customer index, [(product name, qty)], final fulfilment status, payment method)
    DEMO_ORDERS = [
        (120, 0, [("NARS BB Cream", 1)], "delivered", "card"),
        (95, 1, [("Apple Watch", 1)], "delivered", "bank_transfer"),
        (80, 0, [("Matte Lipstick", 2), ("Coco Chanel Perfume", 1)], "delivered", "card"),
        (66, 2, [("White Teddy Bear", 1)], "returned", "card"),
        (50, 3, [("Selfie Stick", 1), ("Black Earbuds", 1)], "delivered", "paypal"),
        (35, 0, [("Jimmy Choo Heels", 1)], "shipped", "card"),
        (21, 4, [("Christian Dior Bag", 1)], "cancelled", "card"),
        (12, 1, [("NARS BB Cream", 2)], "packed", "card"),
        (6, 2, [("Golden Teapot", 1), ("Dining Table", 1)], "processing", "bank_transfer"),
        (3, 3, [("Chocolate Balls", 3)], "pending", "card"),
        (1, 4, [("Silver Jewellery", 1)], "pending", "card"),
        (0, 0, [("LED Console", 1)], "pending", "card"),
    ]

    # A second market so region scoping is visible: pk.<domain> / X-Region: PK shows
    # only this store; the default market never sees it.
    PK_PRODUCTS = [
        ("Kajal Eye Pencil", "makeup", "L'Oréal Paris", 89900, 119900, {"Shade": ["Black", "Brown"]}, "Smudge-proof kajal for all-day definition."),
        ("Rose Water Toner", "skincare", "NARS", 149900, None, {"Size": ["120 ml"]}, "Pure rose water to refresh and hydrate."),
        ("Lawn Print Scarf", "fashion", "ZARA", 249900, 349900, {"Color": ["Teal", "Mustard"]}, "Lightweight lawn scarf with a hand-block print."),
    ]

    def _seed_second_market(self, brands):
        from markets.models import Country

        country = Country.objects.filter(is_active=True, is_default=False).order_by("sort_order").first()
        if country is None:
            return
        owner, _ = User.objects.get_or_create(email=f"seller.{country.code.lower()}@asknget.demo",
                                              defaults={"full_name": "Sana Malik", "is_seller": True, "phone": "+923219876543"})
        owner.set_password(PASSWORD)
        owner.email_verified_at = owner.email_verified_at or timezone.now()
        owner.save()
        app, _ = SellerApplication.objects.get_or_create(
            user=owner,
            defaults=dict(account_type="sole_proprietor", status="active", application_number=f"ASK-SLR-00{country.code}1", current_step=5,
                          country=country, first_name="Sana", last_name="Malik", store_display_name="Karachi Cosmetics",
                          submitted_at=timezone.now(), approved_at=timezone.now(), activated_at=timezone.now()),
        )
        store, _ = Store.objects.get_or_create(
            application=app, defaults=dict(owner=owner, country=country, display_name="Karachi Cosmetics", slug="karachi-cosmetics", status="active",
                                           description="Everyday beauty and lawn essentials.", activated_at=timezone.now()),
        )
        for name, cat_slug, brand, price, compare, options, blurb in self.PK_PRODUCTS:
            category = Category.objects.filter(slug=cat_slug).first() or Category.objects.filter(parent__isnull=True).first()
            product, was_created = Product.objects.get_or_create(
                slug=slugify(name),
                defaults=dict(store=store, country=country, currency=country.currency, category=category, brand=brands.get(brand), name=name,
                              price_cents=price, compare_at_price_cents=compare, short_description=blurb, description=blurb,
                              status="active", published_at=timezone.now()),
            )
            if not was_created:
                continue
            combos = [[]]
            for i, (opt, values) in enumerate(options.items()):
                ot = ProductOptionType.objects.create(product=product, name=opt, sort_order=i)
                vals = [ProductOptionValue.objects.create(option_type=ot, value=v, sort_order=j) for j, v in enumerate(values)]
                combos = [c + [v] for c in combos for v in vals]
            for i, combo in enumerate(combos):
                variant = ProductVariant.objects.create(product=product, sku=f"{slugify(name)[:20].upper()}-{i:02d}", stock_qty=15)
                variant.option_values.set(combo)
            refresh_stock_status(product)

    # The demo ZA store is premium so Chat and Advanced Analytics are visible;
    # the PK store stays standard so the locked state is demoable too.
    DEMO_CHAT = [
        ("customer", "Hi! Is the BB Cream shade Natural suitable for oily skin?"),
        ("seller", "Hello! Yes — it has a matte finish and works well on oily skin."),
        ("customer", "Great, I just ordered one. When will it ship?"),
        ("seller", "It goes out tomorrow morning; you will get the tracking link by email."),
    ]

    def _seed_chat(self, store, seller, member):
        from chat.models import Conversation
        from chat.services import send

        if not store.is_premium:
            store.is_premium = True
            store.save(update_fields=["is_premium"])
        convo, created = Conversation.objects.get_or_create(store=store, customer=member)
        if not created:
            return
        for role, body in self.DEMO_CHAT:
            send(convo, role=role, sender=member if role == "customer" else seller, body=body)

    def _seed_orders(self, store, seller, member):
        if Order.objects.filter(order_number__startswith="ASK-DEMO").exists():
            return
        Fulfilment = OrderItem.FulfilmentStatus
        steps = {
            "pending": [], "processing": ["processing"], "packed": ["processing", "packed"],
            "shipped": ["processing", "packed", "shipped"], "delivered": ["processing", "packed", "shipped", "delivered"],
            "returned": ["processing", "packed", "shipped", "delivered", "returned"], "cancelled": ["cancelled"],
        }
        products = {p.name: p for p in Product.objects.filter(store=store).prefetch_related("variants")}
        now = timezone.now()
        for n, (days_ago, who, lines, final, method) in enumerate(self.DEMO_ORDERS, start=1):
            first, last, email = self.DEMO_CUSTOMERS[who]
            user = member if who == 0 else None  # Olivia is the signed-in demo customer
            items = [(products[name], qty) for name, qty in lines if name in products]
            if not items:
                continue
            subtotal = sum(p.price_cents * q for p, q in items)
            placed_at = now - timedelta(days=days_ago, hours=n % 7)
            order = Order.objects.create(
                user=user, status=Order.OrderStatus.CONFIRMED, order_number=f"ASK-DEMO{n:03d}",
                country=store.country, return_window_days=store.country.return_window_days,
                contact_first_name=first, contact_last_name=last, contact_email=member.email if user else email, contact_phone="+27 82 123 4567",
                shipping_address={"full_name": f"{first} {last}", "street_address": "123 Main Street", "city": "Cape Town", "postal_code": "6001", "country": store.country_id},
                subtotal_cents=subtotal, shipping_cents=700, total_cents=subtotal + 700, currency=items[0][0].currency,
                payment_method_type=method, payment_status=Order.OrderPaymentStatus.PAID,
            )
            Order.objects.filter(pk=order.pk).update(placed_at=placed_at, created_at=placed_at)
            for p, q in items:
                v = p.variants.first()
                OrderItem.objects.create(order=order, store=store, product=p, variant=v, product_name=p.name, variant_summary=v.summary(),
                                         qty=q, unit_price_cents=p.price_cents, line_total_cents=p.price_cents * q, status=Fulfilment.PENDING)
            order_services._record_event(order, Order.OrderStatus.PENDING_PAYMENT, actor="buyer", actor_user=user, note="Order placed")
            order_services._record_event(order, Order.OrderStatus.CONFIRMED, actor="system", note="Payment confirmed")
            for i, status in enumerate(steps[final], start=1):
                order_services.set_store_status(order, store, status=status, actor_user=seller, note="")
                # spread the timeline over the following hours/days
                order.status_events.filter(store=store).order_by("-created_at").update(created_at=placed_at + timedelta(hours=6 * i))
            order.status_events.update(created_at=models.Case(
                models.When(status=Order.OrderStatus.PENDING_PAYMENT, then=models.Value(placed_at)),
                models.When(status=Order.OrderStatus.CONFIRMED, store__isnull=True, then=models.Value(placed_at + timedelta(minutes=3))),
                default=models.F("created_at"),
            ))
        self.stdout.write(f"Seeded {len(self.DEMO_ORDERS)} demo orders for {store.display_name}")

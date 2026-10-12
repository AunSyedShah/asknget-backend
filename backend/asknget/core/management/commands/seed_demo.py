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
from orders.models import Order, OrderDelivery, OrderItem, OrderStatusEvent, Voucher
from sellers import services as seller_services
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
        seller_services.ensure_default_delivery_options(store)   # approve() does this for a real store

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
        self._seed_team_and_money(store, seller, admin)
        self._seed_returns(store, seller, customer)

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
        seller_services.ensure_default_delivery_options(store)
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

    def _seed_team_and_money(self, store, owner, staff):
        """A small team, the ledger for the demo orders (with one payout and one adjustment) and a few
        notifications, so Team, Transactions and the bell have something to show. Idempotent."""
        from django.core.management import call_command

        from ledger import services as ledger
        from ledger.models import LedgerEntry, Payout
        from notifications import services as notifications
        from notifications.models import StoreNotification
        from team.models import StoreMember, TeamRole

        now = timezone.now()
        for name, email, role_key in (("Sarah Lewis", "sarah.lewis@asknget.demo", "manager"),
                                      ("James Peters", "james.peters@asknget.demo", "inventory-manager"),
                                      ("Emma Roberts", "emma.roberts@asknget.demo", "order-manager")):
            user, _ = User.objects.get_or_create(email=email, defaults={"full_name": name})
            user.set_password(PASSWORD)
            user.email_verified_at = user.email_verified_at or now
            user.save()
            role = TeamRole.objects.get(key=role_key)
            StoreMember.objects.get_or_create(store=store, email=email, defaults=dict(
                user=user, role=role, permissions=role.permissions, status="active", joined_at=now, invited_by=owner))
        finance = TeamRole.objects.get(key="finance-manager")
        StoreMember.objects.get_or_create(store=store, email="mia.taylor@asknget.demo", defaults=dict(
            role=finance, permissions=finance.permissions, status="invited", invited_by=owner, invite_expires_at=now + timedelta(days=7)))

        # Money for the demo orders. They were written straight to the tables, so post them now, and let the
        # ones delivered long ago have finished their return window, as they would have in real time.
        call_command("backfill_ledger", verbosity=0)
        for entry in LedgerEntry.objects.filter(store=store, available_at__gt=now, order__isnull=False).select_related("order"):
            delivered = OrderStatusEvent.objects.filter(order=entry.order, store=store, status="delivered").order_by("created_at").first()
            if delivered:
                entry.available_at = delivered.created_at + timedelta(days=entry.order.return_window_days)
                entry.save(update_fields=["available_at", "updated_at"])
        if not LedgerEntry.objects.filter(store=store, type="adjustment").exists():
            ledger.adjust(store, amount_cents=2500, note="Goodwill credit for a late delivery", by=staff)
        if not Payout.objects.filter(store=store).exists():
            available = ledger.balances(store)["available_cents"]
            if available > 1000:
                payout = ledger.create_payout(store, amount_cents=available // 2, note="Weekly payout", by=staff)
                ledger.set_payout_status(payout, "completed", staff)
        if not StoreNotification.objects.filter(store=store).exists() or not StoreNotification.objects.filter(store=store, kind="low_stock").exists():
            latest = Order.objects.filter(items__store=store).order_by("-placed_at").first()
            if latest:
                notifications.notify(store, "order_received", {"order": latest.order_number, "customer": f"{latest.contact_first_name} {latest.contact_last_name}"},
                                     link=("order", latest.pk))
                notifications.notify(store, "new_customer", {"customer": f"{latest.contact_first_name} {latest.contact_last_name}"}, link=("order", latest.pk))
            product = Product.objects.filter(store=store).first()
            if product:
                notifications.notify(store, "low_stock", {"product": product.name, "qty": 8}, link=("product", product.pk))

    def _seed_returns(self, store, seller, customer):
        """Three customer returns in different states (waiting for the seller, on its way back, refunded) so the
        Returns screens have something to show. They go through the real services, so the timeline, stock and
        ledger are what a real return would leave. Idempotent."""
        from io import BytesIO

        from django.core.files.uploadedfile import SimpleUploadedFile
        from PIL import Image

        from orders import services as order_services
        from returns import services as returns
        from returns.models import ReturnRequest
        from returns.policy import terms_for

        if ReturnRequest.objects.filter(store=store).exists():
            return

        def photo():
            buf = BytesIO()
            Image.new("RGB", (8, 8), "grey").save(buf, format="PNG")
            return SimpleUploadedFile("photo.png", buf.getvalue(), content_type="image/png")

        def find(product_name):
            return (OrderItem.objects.filter(store=store, product_name=product_name, status="delivered", order__user__isnull=False)
                    .select_related("order", "product").order_by("-order__placed_at").first())

        Order.objects.filter(order_number="ASK-DEMO002", user__isnull=True).update(user=customer)   # a guest order cannot be returned
        plan = [("Apple Watch", "damaged", "requested"), ("Coco Chanel Perfume", "wrong_item", "approved"), ("NARS BB Cream", "damaged", "refunded")]
        for name, reason, goal in plan:
            item = find(name)
            if item is None:
                continue
            order = item.order
            # Demo orders were written straight to the tables, so give their lines the terms checkout would have recorded.
            terms = terms_for(order.country, {i.product.category_id for i in order.items.select_related("product")})
            for line in order.items.select_related("product"):
                OrderItem.objects.filter(pk=line.pk).update(return_terms=terms[line.product.category_id].as_dict())
            user = order.user
            req = returns.request_return(user, order_services.get_my_order(user, order.pk), item_ids=[item.pk], reason_code=reason,
                                         note="Demo return", files=[photo()])
            if goal in ("approved", "refunded"):
                returns.approve(req, actor="seller", user=seller)
            if goal == "approved":
                returns.add_tracking(req, user=user, tracking_number="TCS 4409 1182", courier="TCS")
            if goal == "refunded":
                returns.mark_received(req, user=seller, condition="good")

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
        options = list(store.delivery_options.filter(is_active=True))
        # Synthetic, and only so the Sales by Channel donut is not a single slice:
        # nothing in the app sets `channel` until the clients start sending it (Q51).
        channels = ["web", "web", "mobile_app", "web", "other"]   # Order.channel uses plain literals (orders/schemas.py)
        for n, (days_ago, who, lines, final, method) in enumerate(self.DEMO_ORDERS, start=1):
            first, last, email = self.DEMO_CUSTOMERS[who]
            user = member if who == 0 else None  # Olivia is the signed-in demo customer
            items = [(products[name], qty) for name, qty in lines if name in products]
            if not items:
                continue
            subtotal = sum(p.price_cents * q for p, q in items)
            placed_at = now - timedelta(days=days_ago, hours=n % 7)
            # The seller's own option, rotated so orders differ (sprint 6).
            option = options[n % len(options)] if options else None
            shipping = option.price_cents if option else 0
            order = Order.objects.create(
                user=user, status=Order.OrderStatus.CONFIRMED, order_number=f"ASK-DEMO{n:03d}",
                country=store.country, return_window_days=store.country.return_window_days,
                contact_first_name=first, contact_last_name=last, contact_email=member.email if user else email, contact_phone="+27 82 123 4567",
                shipping_address={"full_name": f"{first} {last}", "street_address": "123 Main Street", "city": "Cape Town", "postal_code": "6001", "country": store.country_id},
                subtotal_cents=subtotal, shipping_cents=shipping, total_cents=subtotal + shipping, currency=items[0][0].currency,
                payment_method_type=method, payment_status=Order.OrderPaymentStatus.PAID,
                channel=channels[n % len(channels)],
            )
            if option is not None:
                OrderDelivery.objects.create(order=order, store=store, option=option, name=option.name,
                                             eta_label=option.eta_label, kind=option.kind, price_cents=option.price_cents)
            Order.objects.filter(pk=order.pk).update(placed_at=placed_at, created_at=placed_at)
            for p, q in items:
                v = p.variants.first()
                OrderItem.objects.create(order=order, store=store, product=p, variant=v, product_name=p.name, variant_summary=v.summary(),
                                         qty=q, unit_price_cents=p.price_cents, line_total_cents=p.price_cents * q, status=Fulfilment.PENDING)
            order_services._record_event(order, Order.OrderStatus.PENDING_PAYMENT, actor="buyer", actor_user=user, note="Order placed")
            order_services._record_event(order, Order.OrderStatus.CONFIRMED, actor="system", note="Payment confirmed")
            for i, status in enumerate(steps[final], start=1):
                order_services.set_store_status(order, store, status=status, actor_user=seller, note="")
                # Spread the timeline so the fulfilment report has real durations.
                # `.order_by(...).update()` would rewrite every event (no LIMIT on UPDATE),
                # so backdate the one row this step just created.
                latest = order.status_events.filter(store=store).order_by("-created_at", "-id").first()
                if latest is not None:
                    hours = {"processing": 4, "packed": 16, "shipped": 26, "delivered": 74, "returned": 170, "cancelled": 5}.get(status, 6 * i)
                    order.status_events.filter(pk=latest.pk).update(created_at=placed_at + timedelta(hours=hours))
            order.status_events.update(created_at=models.Case(
                models.When(status=Order.OrderStatus.PENDING_PAYMENT, then=models.Value(placed_at)),
                models.When(status=Order.OrderStatus.CONFIRMED, store__isnull=True, then=models.Value(placed_at + timedelta(minutes=3))),
                default=models.F("created_at"),
            ))
        self.stdout.write(f"Seeded {len(self.DEMO_ORDERS)} demo orders for {store.display_name}")

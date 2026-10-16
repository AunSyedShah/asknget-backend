"""Pictures, banners and per-market contact details for the DEMO data, so a storefront built from the design has something to show.

    python manage.py seed_demo_media --yes-this-is-staging

Uploads cannot be kept on a Vercel deployment (its disk is read-only), so the pictures here are links to placeholder photos
(picsum.photos, a different but stable photo per name), stored in the image fields as full web addresses (core.storage). It only adds
what is missing and never changes a picture that already exists, so it is safe to run again. Never run it against production: the
products there are real, and these pictures are not theirs (hence the flag).
"""

from django.core.management.base import BaseCommand, CommandError

from catalog.models import Category, Product, ProductImage
from marketing.models import Banner
from markets.models import Country

PHOTO = "https://picsum.photos/seed/{seed}/{w}/{h}"

# The landing page of the design: a five-slide hero and four promo tiles under "Top Discounted".
HERO = [
    # eyebrow (the small line), title (the headline), subtitle, badge, button label, button link
    ("New Collection 2026", "Carry What Matters", "Bags and everyday essentials, made to last", "", "Shop Now", "/products?sort=new"),
    ("Beauty, Made Simple", "Your Everyday Glow", "Skincare and makeup picks for every day", "", "Shop Beauty", "/products?category=beauty"),
    ("Home Essentials", "Make It Feel Like Home", "Small changes that make a house a home", "", "Shop Home", "/products?category=home-lifestyle"),
    ("Gadgets You Will Use", "Tech That Keeps Up", "Audio, wearables and everyday tech", "", "Shop Electronics", "/products?category=electronics"),
    ("Delivery", "Free Over a Set Amount", "Look for the free-delivery badge in your cart", "", "See Deals", "/products?sort=discount"),
]
TILES = [
    ("TECH ESSENTIALS", "Headphones, chargers and more", "UP TO 50% OFF", "Shop Tech", "/products?category=electronics"),
    ("HOME EDIT", "Refresh a room for less", "UP TO 40% OFF", "Shop Home", "/products?category=home-lifestyle"),
    ("NEW SEASON", "Fresh looks have arrived", "NEW IN", "Shop Fashion", "/products?category=fashion"),
    ("Beauty Edit", "Everyday favourites", "UP TO 30% OFF", "Shop Beauty", "/products?category=beauty"),
]

SITE_INFO = {
    "ZA": {
        "contact_phone": "+27 11 555 0123", "contact_email": "za@asknget.com",
        "address": "12 Rivonia Road, Sandton, Johannesburg 2196, South Africa",
        "social": {"facebook": "https://facebook.com/asknget.za", "instagram": "https://instagram.com/asknget.za",
                   "pinterest": "https://pinterest.com/asknget", "youtube": "https://youtube.com/@asknget"},
    },
    "PK": {
        "contact_phone": "+92 21 3555 0123", "contact_email": "pk@asknget.com",
        "address": "Plot 21, Shahrah-e-Faisal, Karachi 75350, Pakistan",
        "social": {"facebook": "https://facebook.com/asknget.pk", "instagram": "https://instagram.com/asknget.pk",
                   "pinterest": "https://pinterest.com/asknget", "youtube": "https://youtube.com/@asknget"},
    },
}


class Command(BaseCommand):
    help = "Add placeholder pictures, landing-page banners and per-market contact details to the demo data (staging only)."

    def add_arguments(self, parser):
        parser.add_argument("--yes-this-is-staging", action="store_true", help="Required: never run this against production")

    def handle(self, *args, **options):
        if not options["yes_this_is_staging"]:
            raise CommandError("Refusing: this decorates every product and category that has no picture. Pass --yes-this-is-staging, and only against the staging database.")
        made = {"product pictures": 0, "category pictures": 0, "banners": 0, "markets with contact details": 0}

        have = set(ProductImage.objects.values_list("product_id", flat=True))
        for product in Product.objects.exclude(pk__in=have):
            for i in range(3):                                           # a main picture and two for the gallery
                ProductImage.objects.create(product=product, image=PHOTO.format(seed=f"{product.slug}-{i + 1}", w=800, h=800),
                                            alt=product.name, sort_order=i, is_primary=(i == 0))
                made["product pictures"] += 1

        for category in Category.objects.filter(image="") | Category.objects.filter(image__isnull=True):
            category.image = PHOTO.format(seed=f"category-{category.slug}", w=400, h=400)
            category.save(update_fields=["image", "updated_at"])
            made["category pictures"] += 1

        if not Banner.objects.exists():
            for i, (eyebrow, title, subtitle, badge, cta, url) in enumerate(HERO):
                Banner.objects.create(placement="hero", eyebrow=eyebrow, title=title, subtitle=subtitle, badge_text=badge, cta_label=cta, cta_url=url,
                                      image=PHOTO.format(seed=f"banner-hero-{i + 1}", w=1600, h=600), sort_order=i)
                made["banners"] += 1
            for i, (eyebrow, title, badge, cta, url) in enumerate(TILES):
                Banner.objects.create(placement="top_discount", eyebrow=eyebrow, title=title, badge_text=badge, cta_label=cta, cta_url=url,
                                      image=PHOTO.format(seed=f"banner-tile-{i + 1}", w=600, h=400), sort_order=i)
                made["banners"] += 1

        for country in Country.objects.filter(code__in=SITE_INFO, site_info={}):
            country.site_info = SITE_INFO[country.code]
            country.save(update_fields=["site_info", "updated_at"])
            made["markets with contact details"] += 1

        self.stdout.write("Added: " + ", ".join(f"{n} {what}" for what, n in made.items()))

"""Give every existing store the delivery options the design shows.

Shipping used to be platform-wide (`orders.DeliveryMethod`, one catalogue per
market). Sellers now define their own, so each store needs a starting set or its
products become unbuyable. Prices come from that store's market where a platform
method exists, so nothing a buyer can do today changes price.
"""

from django.db import migrations

# name, eta, kind, which platform method to take the price from, fallback cents
DEFAULTS = [
    ("Express Delivery", "1-3 business days", "delivery", "Express Delivery", 1999),
    ("Seller Delivery", "2-5 business days", "delivery", "Standard Delivery", 1099),
    ("Customer Pickup", "Pickup from store", "pickup", None, 0),
]


def seed(apps, schema_editor):
    Store = apps.get_model("sellers", "Store")
    StoreDeliveryOption = apps.get_model("sellers", "StoreDeliveryOption")
    DeliveryMethod = apps.get_model("orders", "DeliveryMethod")

    for store in Store.objects.all().iterator():
        if StoreDeliveryOption.objects.filter(store=store).exists():
            continue
        platform = {m.name: m for m in DeliveryMethod.objects.filter(country_id=store.country_id, is_active=True)}
        for order, (name, eta, kind, source, fallback) in enumerate(DEFAULTS):
            method = platform.get(source) if source else None
            StoreDeliveryOption.objects.create(
                store=store, name=name, eta_label=eta, kind=kind, sort_order=order,
                price_cents=method.price_cents if method else fallback,
            )


def unseed(apps, schema_editor):
    apps.get_model("sellers", "StoreDeliveryOption").objects.all().delete()


class Migration(migrations.Migration):
    dependencies = [
        ("sellers", "0007_seller_delivery"),
        ("orders", "0006_seller_delivery"),
    ]

    operations = [migrations.RunPython(seed, unseed)]

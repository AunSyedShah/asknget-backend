from django.db import migrations

# A snapshot, not an import: later changes to the wording in code must not silently rewrite a
# migration's history, and rows an admin has edited are never overwritten (get_or_create).
TEMPLATES = [
    ("order_received", "orders", "New Order Received", "Order #{order} has been placed by {customer}.", "When a paid order reaches the store. Placeholders: order, customer"),
    ("payment_received", "payments", "Payment Received", "Payment for Order #{order} has been successfully received.", "With a new paid order. Placeholders: order, customer"),
    ("order_cancelled_customer", "orders", "Order Cancelled", "Order #{order} has been cancelled by the customer.", "The buyer cancelled a paid order. Placeholders: order"),
    ("order_cancelled_staff", "orders", "Order Cancelled", "Order #{order} has been cancelled by asknget.", "Staff cancelled a paid order. Placeholders: order"),
    ("low_stock", "inventory", "Low Stock Alert", "{product} has only {qty} units remaining.", "Once, when a live product's stock drops to its low-stock level. Placeholders: product, qty"),
    ("out_of_stock", "inventory", "Out of Stock", "{product} is out of stock.", "Once, when a live product sells out. Placeholders: product"),
    ("new_customer", "customers", "New Customer", "{customer} placed their first order with your store.", "A customer's first paid order at this store. Placeholders: customer, order"),
    ("payout_processed", "payments", "Payout Processed", "Your payout of {amount} has been processed successfully.", "Staff completed a payout. Placeholders: amount"),
    ("product_approved", "system", "Product Approved", "{product} is now live in your store.", "Staff approved a product. Placeholders: product"),
    ("product_rejected", "system", "Product Rejected", "{product} was not approved: {reason}", "Staff rejected a product. Placeholders: product, reason"),
    ("plan_changed", "system", "Plan Updated", "Your store is now on the {plan} plan.", "Staff changed the store's plan. Placeholders: plan"),
]


def seed(apps, schema_editor):
    NotificationTemplate = apps.get_model("notifications", "NotificationTemplate")
    for kind, category, title, body, description in TEMPLATES:
        NotificationTemplate.objects.get_or_create(
            kind=kind, defaults={"category": category, "title": {"en": title}, "body": {"en": body}, "description": description},
        )


class Migration(migrations.Migration):
    dependencies = [("notifications", "0001_initial")]

    operations = [migrations.RunPython(seed, migrations.RunPython.noop)]

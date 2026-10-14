from django.db import migrations

TEMPLATES = [
    ("category_request_approved", "system", "Category Approved",
     "Your request for the category {name} was approved: you can now list products in {category}.",
     "Staff approved a seller's category request. Placeholders: name (what was asked for), category (what the seller got)"),
    ("category_request_rejected", "system", "Category Request Declined",
     "Your request for the category {name} was declined: {reason}",
     "Staff rejected a seller's category request. Placeholders: name, reason"),
]


def seed(apps, schema_editor):
    NotificationTemplate = apps.get_model("notifications", "NotificationTemplate")
    for kind, category, title, body, description in TEMPLATES:
        NotificationTemplate.objects.get_or_create(
            kind=kind, defaults={"category": category, "title": {"en": title}, "body": {"en": body}, "description": description},
        )


class Migration(migrations.Migration):
    dependencies = [("notifications", "0004_seed_return_templates")]

    operations = [migrations.RunPython(seed, migrations.RunPython.noop)]

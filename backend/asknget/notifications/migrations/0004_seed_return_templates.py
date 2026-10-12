from django.db import migrations

TEMPLATES = [
    ("return_requested", "returns", "Return Requested", "{customer} asked to return {items} item(s) from order #{order}: {reason}.", "A buyer asked to return items. Placeholders: order, customer, items, reason"),
    ("return_cancelled", "returns", "Return Withdrawn", "{customer} withdrew the return request for order #{order}.", "The buyer withdrew a request. Placeholders: order, customer"),
    ("return_escalated", "returns", "Return Disputed", "{customer} disputed your rejection of the return for order #{order}. asknget will decide.", "The buyer escalated a rejected return to staff. Placeholders: order, customer"),
    ("return_decided", "returns", "Return Decision", "asknget decided the return for order #{order}: {outcome}.", "Staff decided an escalated or disputed return. Placeholders: order, outcome"),
]


def seed(apps, schema_editor):
    NotificationTemplate = apps.get_model("notifications", "NotificationTemplate")
    for kind, category, title, body, description in TEMPLATES:
        NotificationTemplate.objects.get_or_create(
            kind=kind, defaults={"category": category, "title": {"en": title}, "body": {"en": body}, "description": description},
        )


class Migration(migrations.Migration):
    dependencies = [("notifications", "0003_alter_notificationtemplate_category")]

    operations = [migrations.RunPython(seed, migrations.RunPython.noop)]

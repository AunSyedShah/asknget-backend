from django.db import migrations

# A snapshot, not an import: later edits to these defaults in code must not rewrite a migration, and
# rows an admin has already edited are never overwritten (get_or_create).
REASONS = [
    ("changed_mind", "I changed my mind", "change_of_mind", False),
    ("wrong_size", "It doesn't fit or the shade isn't right", "change_of_mind", False),
    ("damaged", "It arrived damaged", "defect", True),
    ("defective", "It doesn't work properly", "defect", True),
    ("missing_parts", "Parts or accessories are missing", "defect", True),
    ("wrong_item", "I received the wrong item", "wrong_item", True),
    ("not_as_described", "It is not as described", "not_as_described", False),
    ("other", "Other", "other", False),
]


def seed(apps, schema_editor):
    ReturnReason = apps.get_model("returns", "ReturnReason")
    for order, (code, label, kind, photo) in enumerate(REASONS):
        ReturnReason.objects.get_or_create(code=code, defaults={"label": {"en": label}, "kind": kind, "requires_photo": photo, "sort_order": order})


class Migration(migrations.Migration):
    dependencies = [("returns", "0001_initial")]

    operations = [migrations.RunPython(seed, migrations.RunPython.noop)]

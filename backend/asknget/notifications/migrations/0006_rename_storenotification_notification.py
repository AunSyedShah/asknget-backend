"""A store's notification becomes one kind of notification (customers and staff get them too, sprint 9). Rows are kept."""

from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("notifications", "0005_seed_category_request_templates"),
        ("sellers", "0001_initial"),
    ]

    operations = [migrations.RenameModel("StoreNotification", "Notification")]

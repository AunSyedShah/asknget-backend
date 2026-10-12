from django.db import migrations

# (key, name, description, counts_as_manager, {section: level}). Starting points taken from the
# design's Member Details page (a Manager: View on Dashboard, Analytics and Transactions,
# View + Manage elsewhere). They are rows, so the owner edits or adds roles in admin.
ROLES = [
    ("manager", "Manager", "Runs the store day to day.", True,
     {"dashboard": "view", "orders": "manage", "products": "manage", "inventory": "manage", "customers": "manage",
      "marketing": "manage", "analytics": "view", "transactions": "view"}),
    ("inventory-manager", "Inventory Manager", "Keeps stock accurate.", True,
     {"dashboard": "view", "products": "view", "inventory": "manage"}),
    ("order-manager", "Order Manager", "Processes and ships orders.", True,
     {"dashboard": "view", "orders": "manage", "inventory": "view", "customers": "view"}),
    ("marketing-manager", "Marketing Manager", "Runs promotions and watches performance.", True,
     {"dashboard": "view", "products": "view", "marketing": "manage", "analytics": "view"}),
    ("finance-manager", "Finance Manager", "Reconciles sales and payouts.", True,
     {"dashboard": "view", "orders": "view", "analytics": "view", "transactions": "manage"}),
    ("support-agent", "Support Agent", "Answers customers.", False,
     {"dashboard": "view", "orders": "view", "products": "view", "customers": "manage"}),
]


def seed_roles(apps, schema_editor):
    TeamRole = apps.get_model("team", "TeamRole")
    levels = ("dashboard", "orders", "products", "inventory", "customers", "marketing", "analytics", "transactions")
    for order, (key, name, description, manager, perms) in enumerate(ROLES):
        TeamRole.objects.get_or_create(key=key, defaults={
            "name": name, "description": description, "counts_as_manager": manager, "sort_order": order,
            "permissions": {s: perms.get(s, "none") for s in levels},
        })


class Migration(migrations.Migration):
    dependencies = [("team", "0001_initial")]

    operations = [migrations.RunPython(seed_roles, migrations.RunPython.noop)]

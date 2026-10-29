"""Starter roles, and the Administrator role for every staff account that existed before roles did.

Before this, any staff account could use every /admin endpoint. Giving existing non-superuser staff the Administrator role keeps
that, so nobody loses access on deploy; admins narrow it afterwards. Roles that already exist (same name) are left alone.
"""

from django.contrib.auth.management import create_permissions
from django.db import migrations

ROLES = {
    "Administrator": None,   # every staff permission
    "KYC reviewer": ["sellers.view", "sellers.review"],
    "Catalogue": ["products.view", "products.review", "category_requests.view", "category_requests.decide"],
    "Support": ["orders.view", "orders.manage", "returns.view", "returns.decide", "chat.moderate"],
    "Finance": ["orders.view", "returns.view", "returns.refund", "ledger.view", "ledger.adjust", "payouts.view", "payouts.manage"],
}


def forwards(apps, schema_editor):
    config = apps.get_app_config("staff")
    config.models_module = True                     # permissions are normally created after migrate; we need them now
    create_permissions(config, apps=apps, verbosity=0)
    config.models_module = None

    Group = apps.get_model("auth", "Group")
    Permission = apps.get_model("auth", "Permission")
    User = apps.get_model("accounts", "User")
    staff_perms = Permission.objects.filter(content_type__app_label="staff", content_type__model="staffprofile").exclude(codename__endswith="_staffprofile")  # not Django's own add/change/delete/view
    for name, codes in ROLES.items():
        group, created = Group.objects.get_or_create(name=name)
        if created:
            group.permissions.set(staff_perms if codes is None else staff_perms.filter(codename__in=codes))
    admin = Group.objects.get(name="Administrator")
    for user in User.objects.filter(is_staff=True, is_superuser=False):
        user.groups.add(admin)


class Migration(migrations.Migration):
    dependencies = [
        ("staff", "0001_initial"),
        ("auth", "0012_alter_user_first_name_max_length"),
        ("accounts", "0001_initial"),
    ]

    operations = [migrations.RunPython(forwards, migrations.RunPython.noop)]

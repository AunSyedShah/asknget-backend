from django.db import migrations


def seed_global_commission(apps, schema_editor):
    """A starting point only: 5 %, which is what the first design's numbers implied.
    The owner sets the real rates (globally, per market, category or store) in admin."""
    CommissionRule = apps.get_model("config", "CommissionRule")
    if not CommissionRule.objects.filter(scope="global").exists():
        CommissionRule.objects.create(scope="global", rate_bps=500, note="Starting rate; change in admin")


class Migration(migrations.Migration):
    dependencies = [("config", "0001_initial")]

    operations = [migrations.RunPython(seed_global_commission, migrations.RunPython.noop)]

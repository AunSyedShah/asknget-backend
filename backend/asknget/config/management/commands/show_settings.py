"""Print every tunable setting's effective value and where it came from.

    manage.py show_settings                       # platform-wide
    manage.py show_settings --country PK
    manage.py show_settings --store huda-beauty   # slug; includes the store's market

"What is this actually set to for that seller?" is the question admins ask most, and the
admin list only shows overrides, not the defaults they fall back to.
"""

from django.core.management.base import BaseCommand, CommandError

from config import registry, services
from config.models import CommissionRule
from sellers.models import Store


class Command(BaseCommand):
    help = "Show each setting's effective value and its source (store / country / global / default)."

    def add_arguments(self, parser):
        parser.add_argument("--country", help="ISO code of a market, e.g. PK")
        parser.add_argument("--store", help="store slug")

    def handle(self, *args, country=None, store=None, **options):
        store_obj = None
        if store:
            store_obj = Store.objects.filter(slug=store).first()
            if store_obj is None:
                raise CommandError(f"No store with slug {store!r}")
        keys = sorted(registry.REGISTRY)
        resolved = services.resolve(keys, store=store_obj, country=country.upper() if country else None)
        width = max(len(k) for k in keys)
        where = f"store {store_obj.slug} ({store_obj.country_id})" if store_obj else (f"market {country.upper()}" if country else "platform-wide")
        self.stdout.write(self.style.MIGRATE_HEADING(f"Effective settings — {where}"))
        for key in keys:
            value, source = resolved[key]
            self.stdout.write(f"  {key:<{width}}  {value!r:<16} {source}")
        if store_obj:
            rates = services.commission_rates(store_obj, [None])
            rate = rates[None]
            self.stdout.write(f"\n  commission (no category rule applied)  {rate.percent:g} %" + ("" if rate.rule_id else "  (NO RULE: 0 %)"))
        else:
            self.stdout.write(f"\n  commission rules: {CommissionRule.objects.filter(is_active=True).count()} active")

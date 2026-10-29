"""Choose which markets are live.

    python manage.py set_markets ZA          # only South Africa is active (and the default)
    python manage.py set_markets ZA PK       # both are active; ZA is the default (first code)

Every other market is switched off: the API answers `400 unknown_region` for it until it is switched on again here or in Django
admin (Countries). Nothing is deleted. Used once at the first deploy of a new environment (deploy/ec2), and when a market launches.
"""

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from markets.models import Country


class Command(BaseCommand):
    help = "Switch on the listed markets (the first becomes the default) and switch every other market off."

    def add_arguments(self, parser):
        parser.add_argument("codes", nargs="+", help="Country codes, e.g. ZA PK")

    @transaction.atomic
    def handle(self, *args, codes, **options):
        codes = [c.strip().upper() for c in codes if c.strip()]
        known = set(Country.objects.values_list("code", flat=True))
        unknown = [c for c in codes if c not in known]
        if unknown:
            raise CommandError(f"Unknown market(s): {', '.join(unknown)}. Known: {', '.join(sorted(known)) or 'none (run seed_lookups)'}")
        Country.objects.exclude(code__in=codes).update(is_active=False, is_default=False)
        Country.objects.filter(code__in=codes).update(is_active=True)
        Country.objects.filter(code__in=codes).exclude(code=codes[0]).update(is_default=False)
        Country.objects.filter(code=codes[0]).update(is_default=True)
        self.stdout.write(f"Active markets: {', '.join(codes)} (default {codes[0]}); off: {', '.join(sorted(known - set(codes))) or 'none'}")

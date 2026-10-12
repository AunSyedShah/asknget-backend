from django.core.management.base import BaseCommand

from returns import services


class Command(BaseCommand):
    help = "Ask couriers that support polling where each returned parcel on its way is. Run it from a scheduler (the manual courier has nothing to poll)."

    def handle(self, *args, **options):
        self.stdout.write(f"Updated {services.sync_tracking()} shipment(s)")

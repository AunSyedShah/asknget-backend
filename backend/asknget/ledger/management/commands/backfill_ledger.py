"""Post ledger entries for orders that were paid before the ledger existed.

    manage.py backfill_ledger            # do it
    manage.py backfill_ledger --dry-run  # say what it would do

Idempotent: an order that already has its entries is skipped, so it is safe to run twice or after
a partial run. It uses the commission rules and hold policy in force today, and treats an order
as released from the moment its delivery was recorded (or now, if the record is missing).
Lines that were cancelled or returned are not posted at all: a sale and its refund net to zero.
"""

from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from ledger import services
from ledger.models import LedgerEntry
from orders.models import Order, OrderStatusEvent


class Command(BaseCommand):
    help = "Post sale and fee entries for paid orders that have none."

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true")

    def handle(self, *args, dry_run=False, **options):
        paid = Order.objects.filter(payment_status=Order.OrderPaymentStatus.PAID).exclude(
            pk__in=LedgerEntry.objects.filter(type="sale").values("order_id")
        )
        posted = released = 0
        for order in paid.iterator():
            with transaction.atomic():
                entries = services.post_sales(order)
                if not entries:
                    continue
                posted += len({e.store_id for e in entries})
                delivered = set(OrderStatusEvent.objects.filter(order=order, status="delivered", store__isnull=False).values_list("store_id", flat=True))
                if order.status == Order.OrderStatus.DELIVERED:
                    delivered |= set(order.items.values_list("store_id", flat=True))
                for store_id in delivered:
                    released += services.release(order, store_id)
                if dry_run:
                    transaction.set_rollback(True)
        verb = "would post" if dry_run else "posted"
        self.stdout.write(f"{verb} entries for {posted} store order(s); {released} released from the hold")

"""Post ledger entries for orders that were paid before the ledger existed.

    manage.py backfill_ledger            # do it
    manage.py backfill_ledger --dry-run  # say what it would do

Idempotent: an order that already has its entries is skipped, so it is safe to run twice or after
a partial run. It uses the commission rules and hold policy in force today, and treats a store's
goods as released from the moment its delivery was recorded.

History is kept whole and dated when it happened, so period reports mean something:
a sale and its fee are dated to the order; a cancelled or returned store order gets its sale, fee
and the refund of both (dated to the cancel / return event), netting to zero.
"""

from django.core.management.base import BaseCommand
from django.db import transaction

from ledger import services
from ledger.models import LedgerEntry
from orders.models import Order, OrderItem, OrderStatusEvent

GONE = (OrderItem.FulfilmentStatus.CANCELLED, OrderItem.FulfilmentStatus.RETURNED)


class Command(BaseCommand):
    help = "Post sale and fee entries (and refunds) for paid orders that have none."

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true")

    def handle(self, *args, dry_run=False, **options):
        paid = Order.objects.filter(payment_status=Order.OrderPaymentStatus.PAID).exclude(
            pk__in=LedgerEntry.objects.filter(type="sale").values("order_id")
        )
        posted = released = refunded = 0
        for order in paid.iterator():
            with transaction.atomic():
                entries = services.post_sales(order, include_refunded=True)
                if not entries:
                    continue
                store_ids = {e.store_id for e in entries}
                posted += len(store_ids)
                LedgerEntry.objects.filter(pk__in=[e.pk for e in entries]).update(created_at=order.placed_at)

                delivered = set(OrderStatusEvent.objects.filter(order=order, status="delivered", store__isnull=False).values_list("store_id", flat=True))
                if order.status == Order.OrderStatus.DELIVERED:
                    delivered |= store_ids
                for store_id in delivered & store_ids:
                    released += services.release(order, store_id)

                for store_id in store_ids:
                    statuses = set(order.items.filter(store_id=store_id).values_list("status", flat=True))
                    if statuses <= set(GONE) or order.status == Order.OrderStatus.REFUNDED:
                        returned = services.refund(order, store_id)
                        refunded += 1 if returned else 0
                        event = (
                            OrderStatusEvent.objects.filter(order=order, status__in=["cancelled", "returned", "refunded"])
                            .filter(store_id=store_id).order_by("-created_at").first()
                            or OrderStatusEvent.objects.filter(order=order, status__in=["cancelled", "returned", "refunded"], store__isnull=True)
                            .order_by("-created_at").first()
                        )
                        LedgerEntry.objects.filter(pk__in=[e.pk for e in returned]).update(created_at=event.created_at if event else order.placed_at)
                if dry_run:
                    transaction.set_rollback(True)
        verb = "would post" if dry_run else "posted"
        self.stdout.write(f"{verb} entries for {posted} store order(s); {released} released from the hold; {refunded} refunded")

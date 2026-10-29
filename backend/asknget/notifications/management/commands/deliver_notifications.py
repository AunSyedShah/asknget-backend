"""Send notification e-mails that are due: those that failed to send after their change was saved, and retries.

    python manage.py deliver_notifications            # once (e.g. from a scheduler)
    python manage.py deliver_notifications --watch    # keep going (the worker container)

Safe to run in several places at once: each message is claimed before it is sent, so none goes out twice.
"""

import time

from django.core.management.base import BaseCommand

from notifications.bus import deliver


class Command(BaseCommand):
    help = "Send notification e-mails that are due (and retry failed ones)."

    def add_arguments(self, parser):
        parser.add_argument("--watch", action="store_true", help="Keep running")
        parser.add_argument("--interval", type=int, default=10, help="Seconds between rounds with --watch")

    def handle(self, *args, watch, interval, **options):
        while True:
            result = deliver()
            if any(result.values()) or not watch:
                self.stdout.write(f"sent {result['sent']}, retry later {result['retry']}, gave up {result['failed']}")
            if not watch:
                return
            time.sleep(interval)

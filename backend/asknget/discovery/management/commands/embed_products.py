"""Compute / refresh product embeddings.

    python manage.py embed_products            # missing + stale only (idempotent, safe at start-up)
    python manage.py embed_products --all      # re-embed everything (after changing provider/model)
    python manage.py embed_products --watch    # keep running; re-check every --interval seconds (the `worker` service)
"""

import time

from django.conf import settings
from django.core.management.base import BaseCommand

from discovery.embeddings import get_embedder
from discovery.indexing import embed_products


class Command(BaseCommand):
    help = "Embed products for semantic search and recommendations"

    def add_arguments(self, parser):
        parser.add_argument("--all", action="store_true", help="Re-embed every product, not just missing/stale ones")
        parser.add_argument("--watch", action="store_true", help="Loop forever, embedding stale products as they appear")
        parser.add_argument("--interval", type=int, default=30, help="Seconds between passes in --watch mode")

    def handle(self, *args, **opts):
        embedder = get_embedder()
        self.stdout.write(f"provider={settings.EMBEDDING_PROVIDER} model={embedder.name} dims={settings.EMBEDDING_DIMENSIONS}")
        while True:
            stats = embed_products(force=opts["all"])
            if stats["embedded"] or not opts["watch"]:
                self.stdout.write(f"embedded={stats['embedded']} unchanged={stats['unchanged']} skipped={stats['skipped']}")
            if not opts["watch"]:
                break
            opts["all"] = False
            time.sleep(opts["interval"])

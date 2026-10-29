"""seed_lookups: the category tree it writes, and that running it again changes nothing."""

from io import StringIO

from django.core.management import call_command
from django.test import TestCase
from django.utils.text import slugify

from catalog.models import Category
from core.management.commands.seed_lookups import CATEGORIES


def _names():
    return [name for top, children in CATEGORIES for name in (top, *children)]


class SeedCategoriesTests(TestCase):
    def test_every_name_has_its_own_slug(self):
        # Rows are matched by slug: a repeated slug would move an existing category under another parent.
        slugs = [slugify(n) for n in _names()]
        self.assertEqual(len(slugs), len(set(slugs)), [s for s in slugs if slugs.count(s) > 1])

    def test_the_tree_is_written_as_listed(self):
        call_command("seed_lookups", stdout=StringIO())
        for order, (top, children) in enumerate(CATEGORIES):
            parent = Category.objects.get(slug=slugify(top))
            self.assertIsNone(parent.parent_id, top)
            self.assertEqual(parent.sort_order, order, top)
            self.assertEqual(
                list(Category.objects.filter(parent=parent).order_by("sort_order").values_list("name", flat=True)), children, top,
            )

    def test_running_it_again_creates_nothing_and_keeps_existing_rows(self):
        quiet = StringIO()
        call_command("seed_lookups", stdout=quiet)
        before = dict(Category.objects.values_list("slug", "id"))
        call_command("seed_lookups", stdout=quiet)
        self.assertEqual(dict(Category.objects.values_list("slug", "id")), before)
        self.assertEqual(Category.objects.count(), len(_names()))

"""Flip `ProductEmbedding.is_stale` whenever text that feeds the product card
changes, so the next `embed_products` run re-embeds just those products."""

from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver

from catalog.models import Brand, Product, ProductContentSection, ProductOptionType, ProductOptionValue

from .indexing import mark_stale

CARD_FIELDS = {"name", "brand_id", "category_id", "tags", "short_description", "description_title", "description"}


@receiver(post_save, sender=Product)
def _product_saved(sender, instance, created, update_fields=None, **kwargs):
    if created:
        return  # nothing to mark yet; embed_products picks up rows without an embedding
    if update_fields is not None and not (set(update_fields) & CARD_FIELDS):
        return  # e.g. rating refresh, status flip
    mark_stale([instance.pk])


@receiver(post_save, sender=ProductOptionType)
@receiver(post_delete, sender=ProductOptionType)
@receiver(post_save, sender=ProductContentSection)
@receiver(post_delete, sender=ProductContentSection)
def _child_changed(sender, instance, **kwargs):
    mark_stale([instance.product_id])


@receiver(post_save, sender=ProductOptionValue)
@receiver(post_delete, sender=ProductOptionValue)
def _option_value_changed(sender, instance, **kwargs):
    product_id = ProductOptionType.objects.filter(pk=instance.option_type_id).values_list("product_id", flat=True).first()
    if product_id:
        mark_stale([product_id])


@receiver(post_save, sender=Brand)
def _brand_renamed(sender, instance, created, **kwargs):
    if not created:
        mark_stale(Product.objects.filter(brand=instance).values_list("pk", flat=True))

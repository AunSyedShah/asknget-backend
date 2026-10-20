"""Abstract bases and lookup tables shared by every app."""

import uuid

from django.db import models


class TimeStampedModel(models.Model):
    """UUID primary key + created/updated timestamps. Every concrete model inherits this."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True
        ordering = ["-created_at"]


class SoftDeleteQuerySet(models.QuerySet):
    def alive(self):
        return self.filter(deleted_at__isnull=True)

    def dead(self):
        return self.filter(deleted_at__isnull=False)


class SoftDeleteManager(models.Manager):
    """Default manager hides soft-deleted rows; use `all_objects` to see everything."""

    def get_queryset(self):
        return SoftDeleteQuerySet(self.model, using=self._db).alive()


class SoftDeleteModel(TimeStampedModel):
    deleted_at = models.DateTimeField(null=True, blank=True, db_index=True)

    objects = SoftDeleteManager()
    all_objects = models.Manager()

    class Meta:
        abstract = True

    def soft_delete(self):
        from django.utils import timezone

        self.deleted_at = timezone.now()
        self.save(update_fields=["deleted_at"])


class Bank(TimeStampedModel):
    """Lookup for the seller banking step ("Select your bank")."""

    name = models.CharField(max_length=120)
    country = models.CharField(max_length=2, help_text="ISO 3166-1 alpha-2")
    swift = models.CharField(max_length=11, blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["country", "name"]
        constraints = [
            models.UniqueConstraint(fields=["country", "name"], name="uniq_bank_per_country"),
        ]

    def __str__(self):
        return f"{self.name} ({self.country})"


class SiteSetting(TimeStampedModel):
    """Key/value store for footer contact info, policy blurbs, accepted-payment logos, etc."""

    key = models.SlugField(max_length=100, unique=True)
    value = models.JSONField(default=dict, blank=True)
    description = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ["key"]

    def __str__(self):
        return self.key


def coerce_nulls(model, data: dict) -> dict:
    """Schemas use Optional[...] = None for "not provided"; Django CharFields use "".
    Map None -> "" for any non-nullable field so create()/setattr never violate NOT NULL."""
    nullable = {f.name for f in model._meta.get_fields() if getattr(f, "null", False)}
    return {k: ("" if v is None and k not in nullable else v) for k, v in data.items()}

class StoredFile(models.Model):
    """An uploaded file kept in the database (core.storage.DatabaseStorage), for hosts without a writable disk."""

    name = models.CharField(max_length=255, primary_key=True, help_text="The path the file field stores, e.g. products/ab12.png")
    content = models.BinaryField()
    content_type = models.CharField(max_length=100)
    size = models.PositiveIntegerField()
    etag = models.CharField(max_length=32)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return f"{self.name} ({self.size} bytes)"

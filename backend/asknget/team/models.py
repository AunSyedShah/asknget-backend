"""Who can work in a store besides its owner, with what access, and what they did.

The owner is implicit (`Store.owner`) and never a row here: they cannot be removed,
downgraded or invited, which is a rule the schema can't break because there is nothing to edit.
"""

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models.functions import Lower

from core.models import TimeStampedModel

from . import sections


def validate_permissions(value):
    try:
        sections.normalise(value)
    except ValueError as exc:
        raise ValidationError(str(exc)) from None


class TeamRole(TimeStampedModel):
    """A named preset of access ("Inventory Manager"). Data, so a new role needs no release.
    Choosing one copies its levels onto the member; editing a role later affects only people
    assigned afterwards, so an admin's edit can never silently widen someone's access."""

    key = models.SlugField(max_length=40, unique=True)
    name = models.CharField(max_length=60)
    description = models.CharField(max_length=200, blank=True)
    permissions = models.JSONField(default=dict, validators=[validate_permissions], help_text='{"orders": "manage", "products": "view"}; sections left out mean none')
    counts_as_manager = models.BooleanField(default=False, help_text='Counted in the "Managers" card on the team page')
    is_active = models.BooleanField(default=True)
    sort_order = models.PositiveSmallIntegerField(default=0)

    class Meta(TimeStampedModel.Meta):
        ordering = ["sort_order", "name"]

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        self.permissions = sections.normalise(self.permissions)
        super().save(*args, **kwargs)


class StoreMember(TimeStampedModel):
    class Status(models.TextChoices):
        INVITED = "invited", "Invited (pending)"
        ACTIVE = "active", "Active"
        SUSPENDED = "suspended", "Suspended"

    store = models.ForeignKey("sellers.Store", on_delete=models.CASCADE, related_name="members")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, null=True, blank=True, related_name="store_memberships",
                             help_text="Empty until the invitation is accepted")
    email = models.EmailField(help_text="Where the invitation went; the account that accepts it must use this address")
    role = models.ForeignKey(TeamRole, on_delete=models.SET_NULL, null=True, blank=True, related_name="members")
    permissions = models.JSONField(default=dict, validators=[validate_permissions])
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.INVITED, db_index=True)
    invited_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    invite_token_hash = models.CharField(max_length=64, blank=True, db_index=True, help_text="sha256 of the link's token; the raw token exists only in the e-mail")
    invite_expires_at = models.DateTimeField(null=True, blank=True)
    joined_at = models.DateTimeField(null=True, blank=True)
    last_active_at = models.DateTimeField(null=True, blank=True)

    class Meta(TimeStampedModel.Meta):
        ordering = ["created_at"]
        constraints = [models.UniqueConstraint(Lower("email"), "store", name="uniq_member_email_per_store")]
        indexes = [models.Index(fields=["user", "status"])]

    def __str__(self):
        return f"{self.email} @ {self.store_id}"

    def save(self, *args, **kwargs):
        self.permissions = sections.normalise(self.permissions)
        self.email = self.email.strip().lower()
        super().save(*args, **kwargs)

    def access(self) -> sections.Access:
        return sections.Access(self.permissions)

    @property
    def is_custom(self) -> bool:
        """True once the owner has changed levels away from the role's preset."""
        return self.role_id is None or sections.normalise(self.role.permissions) != sections.normalise(self.permissions)


class StoreActivity(TimeStampedModel):
    """What people did, in words a person can read ("Updated price for Wireless Earbuds Pro").
    Written deliberately by the actions that matter, not by logging requests."""

    store = models.ForeignKey("sellers.Store", on_delete=models.CASCADE, related_name="activity")
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    action = models.SlugField(max_length=60, help_text="update_price, update_inventory, export_orders, ...")
    summary = models.CharField(max_length=255)
    target_type = models.CharField(max_length=40, blank=True)
    target_id = models.CharField(max_length=64, blank=True)

    class Meta(TimeStampedModel.Meta):
        ordering = ["-created_at"]
        verbose_name_plural = "store activity"
        indexes = [models.Index(fields=["store", "-created_at"]), models.Index(fields=["actor", "-created_at"])]

    def __str__(self):
        return self.summary

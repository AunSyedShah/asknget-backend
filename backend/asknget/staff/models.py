from django.conf import settings
from django.db import models

from core.models import TimeStampedModel

from .permissions import CATALOGUE


class StaffProfile(TimeStampedModel):
    """Where a staff member works. Their roles are their Django groups; this adds the markets they may see.

    No profile, or a profile with no markets, means every market. A superuser always sees everything."""

    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="staff_profile")
    markets = models.ManyToManyField("markets.Country", blank=True, related_name="+", help_text="Leave empty for every market")

    class Meta(TimeStampedModel.Meta):
        permissions = [(code, label) for code, label in CATALOGUE.items()]

    def __str__(self):
        return f"{self.user} @ {', '.join(m.code for m in self.markets.all()) or 'every market'}"

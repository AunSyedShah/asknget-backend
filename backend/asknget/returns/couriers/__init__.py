"""The courier seam: where a courier company's API plugs in.

Nothing here talks to a real courier yet - that integration is coming from the technical lead. When it
lands, a new module in this package does three things and nothing else changes:

1. subclass `Courier`, set `slug`, implement `book_return` (and `track` / `cancel` / `parse_webhook` as the
   courier supports them) - credentials come from the environment, never from the database;
2. call `register(MyCourier())` at the bottom of the module and import it in `load()` below;
3. an admin points a market at it: set `returns.courier` = that slug (Django admin > Config > Settings,
   scope "country" for one market or "global").

The buyer flow already calls this layer: approving a return books a pickup through the market's courier, the
result is stored on `ReturnShipment` (tracking number, label, courier id), and tracking updates - polled via
`track` or pushed to `POST /couriers/{slug}/webhook` - move the shipment's status through `apply_update`.
With no courier configured the `manual` one is used: the buyer ships it themselves and enters the tracking number.
The same registry is meant to carry outbound seller shipments later (`book_shipment`); it is not built.
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime

log = logging.getLogger(__name__)


@dataclass
class Booking:
    """What a courier returns when asked to collect a parcel."""

    tracking_number: str = ""
    tracking_url: str = ""
    label_url: str = ""
    external_id: str = ""
    status: str = "pending"          # a ReturnShipment.Status value: "booked" once a pickup is arranged
    raw: dict = field(default_factory=dict)


@dataclass
class TrackingUpdate:
    """One status change reported by a courier (polled, or pushed through a webhook)."""

    status: str                      # a ReturnShipment.Status value
    tracking_number: str = ""        # at least one of these two identifies the shipment
    external_id: str = ""
    at: datetime | None = None
    note: str = ""
    raw: dict = field(default_factory=dict)


class Courier:
    slug = ""

    def book_return(self, request, pickup: dict, dropoff: dict) -> Booking:
        """Arrange collection of the returned parcel from `pickup` (the buyer) to `dropoff` (the store)."""
        raise NotImplementedError

    def cancel(self, shipment) -> None:
        """Cancel a booking that has not been collected. Default: nothing to cancel."""

    def track(self, shipment) -> TrackingUpdate | None:
        """Ask the courier where the parcel is. Default: this courier only pushes (or has no tracking)."""
        return None

    def parse_webhook(self, body: bytes, headers) -> list[TrackingUpdate]:
        """Verify the signature and turn a courier callback into updates. Raise NotImplementedError if there is none."""
        raise NotImplementedError


_REGISTRY: dict[str, Courier] = {}
_loaded = False


def register(courier: Courier) -> Courier:
    if not courier.slug:
        raise ValueError("a courier needs a slug")
    _REGISTRY[courier.slug] = courier
    return courier


def load() -> None:
    """Import every courier module once. Add the new integration's import here."""
    global _loaded
    if not _loaded:
        _loaded = True
        from . import manual  # noqa: F401


def get(slug: str) -> Courier | None:
    load()
    return _REGISTRY.get(slug)


def for_country(country) -> Courier:
    """The courier this market's admin chose, falling back to `manual` (never raises: a typo in the setting
    must not stop a customer returning something)."""
    from config import services as config

    load()
    slug = config.get("returns.courier", country=country)
    courier = _REGISTRY.get(slug)
    if courier is None:
        log.warning("returns.courier=%r is not a registered courier; using 'manual'", slug)
        courier = _REGISTRY["manual"]
    return courier


def available() -> list[str]:
    load()
    return sorted(_REGISTRY)

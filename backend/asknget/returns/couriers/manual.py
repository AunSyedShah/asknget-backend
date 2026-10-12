"""The default "courier": there is none. The buyer posts the parcel themselves and types the tracking number in."""

from . import Booking, Courier, register


class ManualCourier(Courier):
    slug = "manual"

    def book_return(self, request, pickup: dict, dropoff: dict) -> Booking:
        return Booking(status="pending")      # nothing to book; the shipment waits for the buyer's tracking number


register(ManualCourier())

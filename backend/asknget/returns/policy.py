"""What can be returned, and until when.

Two moments:

* **Checkout** snapshots, per order line, what the rules said (`terms_for` -> `OrderItem.return_terms`), so editing a
  rule later never rewrites an order that already exists.
* **Request time** reads that snapshot and the delivery date to decide which lines are open to return for the
  reason the buyer picked (`eligibility`).

Resolution for one line: the nearest category (itself, then its parents) that has an active `ReturnRule` for this
market (or for all markets) overrides the market's `ReturnPolicy`; a rule field left empty keeps the policy's value.
No policy, or an inactive one, means no returns.
"""

from dataclasses import asdict, dataclass
from datetime import datetime, timedelta

from django.db.models import Q
from django.utils import timezone

from config.services import _category_chains
from orders.models import OrderItem, OrderStatusEvent

from .models import ReturnReason, ReturnRequest, ReturnRule

WHY_NOT = ("not_delivered", "window_closed", "not_returnable", "already_requested", "already_returned")


@dataclass(frozen=True)
class Terms:
    change_of_mind_days: int = 0
    defect_days: int = 0
    restocking_fee_bps: int = 0

    def days_for(self, kind: str) -> int:
        return self.change_of_mind_days if kind in (ReturnReason.Kind.CHANGE_OF_MIND, ReturnReason.Kind.OTHER) else self.defect_days

    def as_dict(self) -> dict:
        return asdict(self)


NO_RETURNS = Terms()


def policy_terms(country) -> Terms:
    policy = country.returns
    if policy is None or not policy.is_active:
        return NO_RETURNS
    return Terms(policy.window_days, policy.defect_days, policy.restocking_fee_bps)


def terms_for(country, category_ids) -> dict:
    """{category_id: Terms} for these categories in this market (None is a valid key: a product without a category)."""
    category_ids = list(category_ids)
    base = policy_terms(country)
    if base is NO_RETURNS:
        return {cid: NO_RETURNS for cid in category_ids}
    chains = _category_chains(category_ids)
    wanted = {cid for chain in chains.values() for cid in chain}
    rules = {}
    if wanted:
        for rule in ReturnRule.objects.filter(Q(country__isnull=True) | Q(country=country), category_id__in=wanted, is_active=True):
            key = rule.category_id
            if key not in rules or (rule.country_id and not rules[key].country_id):     # this market's own rule beats the all-markets one
                rules[key] = rule
    out = {}
    for cid in category_ids:
        rule = next((rules[c] for c in chains.get(cid, []) if c in rules), None)
        if rule is None:
            out[cid] = base
        else:
            out[cid] = Terms(
                base.change_of_mind_days if rule.change_of_mind_days is None else rule.change_of_mind_days,
                base.defect_days if rule.defect_days is None else rule.defect_days,
                base.restocking_fee_bps,
            )
    return out


def line_terms(item: OrderItem) -> Terms:
    """The terms in force for a line: its checkout snapshot, or (orders from before returns existed) the order's window."""
    snap = item.return_terms
    if snap:
        return Terms(snap.get("change_of_mind_days", 0), snap.get("defect_days", 0), snap.get("restocking_fee_bps", 0))
    days = item.order.return_window_days
    return Terms(days, days, 0)


def delivered_at(order, store_id) -> datetime | None:
    """When this store's part of the order was delivered (the latest such event; order-level events count too)."""
    times = [e.created_at for e in order.status_events.all() if e.status == "delivered" and e.store_id in (None, store_id)]
    return max(times) if times else None


@dataclass
class LineCheck:
    item: OrderItem
    eligible: bool
    why_not: str            # one of WHY_NOT, or ""
    deadline: datetime | None
    change_of_mind_until: datetime | None
    defect_until: datetime | None


def blocked_item_ids(order) -> set:
    """Lines already in a request that has not been withdrawn."""
    from .models import ReturnItem

    return set(
        ReturnItem.objects.filter(request__order=order).exclude(request__status=ReturnRequest.Status.CANCELLED).values_list("order_item_id", flat=True)
    )


def eligibility(order, kind: str | None = None, *, now: datetime | None = None, store_id=None) -> list[LineCheck]:
    """Each line of the order (or of one store), with whether it may be returned for a reason of this `kind`.
    With no `kind`, a line is eligible if it could be returned for *any* kind (the order page uses that)."""
    now = now or timezone.now()
    items = [i for i in order.items.all() if store_id is None or i.store_id == store_id]
    blocked = blocked_item_ids(order)
    out = []
    for item in items:
        terms = line_terms(item)
        done_at = delivered_at(order, item.store_id) if item.status in ("delivered",) else None
        com_until = done_at + timedelta(days=terms.change_of_mind_days) if done_at and terms.change_of_mind_days else None
        def_until = done_at + timedelta(days=terms.defect_days) if done_at and terms.defect_days else None
        if item.status == "returned":
            why, deadline = "already_returned", None
        elif item.status != "delivered" or done_at is None:
            why, deadline = "not_delivered", None
        elif item.pk in blocked:
            why, deadline = "already_requested", None
        else:
            if kind:
                days = terms.days_for(kind)
                deadline = done_at + timedelta(days=days) if days else None
                why = "not_returnable" if not days else ("window_closed" if now > deadline else "")
            else:
                live = [d for d in (com_until, def_until) if d and now <= d]
                deadline = max(live) if live else max([d for d in (com_until, def_until) if d], default=None)
                why = "" if live else ("window_closed" if deadline else "not_returnable")
        out.append(LineCheck(item, not why, why, deadline, com_until, def_until))
    return out

"""The event catalogue: what can happen, who is told by default, and what each of them is told.

An event is emitted once (`notifications.bus.emit("order.placed", order)`); this file turns it into targets, one per thing to
say to one audience (a customer, one store's team, the staff of one market). Admins change who is told and how through
`NotificationRule` rows; `DEFAULTS` here is what applies until they do, so a new event works without a row.

The wording of each `kind` is a `NotificationTemplate` (seeded from `defaults.TEMPLATES`), edited in Django admin.
"""

from dataclasses import dataclass, field

CUSTOMER, SELLER, STAFF = "customer", "seller", "staff"


@dataclass(frozen=True)
class Default:
    """What an audience gets until an admin says otherwise (the fields of `NotificationRule`)."""

    is_active: bool = True
    in_app: bool = True
    email: bool = False
    realtime: bool = True
    user_can_opt_out: bool = True
    default_on: bool = True
    staff_permission: str = ""
    min_amount_cents: int | None = None


MANDATORY = dict(user_can_opt_out=False)

DEFAULTS: dict[str, dict[str, Default]] = {
    "order.placed": {
        CUSTOMER: Default(email=True, **MANDATORY),
        STAFF: Default(is_active=False, staff_permission="orders.view"),      # "high-value order": an admin sets min_amount and switches it on
    },
    "order.paid": {
        CUSTOMER: Default(email=True, **MANDATORY),
        SELLER: Default(email=True),
    },
    "order.cancelled": {
        CUSTOMER: Default(email=True, **MANDATORY),
        SELLER: Default(email=True),
        STAFF: Default(staff_permission="orders.view"),
    },
    "order.shipped": {CUSTOMER: Default(email=True)},
    "order.out_for_delivery": {CUSTOMER: Default(email=True)},
    "order.delivered": {CUSTOMER: Default(email=True)},
    "return.requested": {
        CUSTOMER: Default(email=True, **MANDATORY),
        SELLER: Default(email=True),
        STAFF: Default(staff_permission="returns.view"),
    },
    "return.cancelled": {SELLER: Default()},
    "return.approved": {CUSTOMER: Default(email=True, **MANDATORY)},
    "return.rejected": {CUSTOMER: Default(email=True, **MANDATORY)},
    "return.no_refund": {CUSTOMER: Default(email=True, **MANDATORY)},
    "return.escalated": {
        SELLER: Default(email=True),
        STAFF: Default(email=True, staff_permission="returns.decide"),
    },
    "return.decided": {
        CUSTOMER: Default(email=True, **MANDATORY),
        SELLER: Default(email=True),
    },
    "refund.issued": {
        CUSTOMER: Default(email=True, **MANDATORY),
        SELLER: Default(),
        STAFF: Default(is_active=False, staff_permission="returns.refund"),
    },
}


@dataclass
class Target:
    """One thing to say to one audience. Exactly one of user/email (customer), store (seller), market (staff) applies."""

    audience: str
    kind: str
    params: dict
    link: tuple[str, object] | None = None
    user: object = None                 # customer with an account
    email: str = ""                     # customer without one (a guest order)
    store_id: object = None             # seller team
    market_id: str | None = None        # staff: whose market (None = every market)
    amount_cents: int | None = None     # for a rule's min_amount
    mail: bool = True                   # False: the bell only, even when the rule e-mails (one e-mail per event is enough)
    extra: dict = field(default_factory=dict)


# --------------------------------------------------------------------------------------
# Builders: subject → targets. Imports are local: notifications must not import the apps that emit.
# --------------------------------------------------------------------------------------


def _name(order) -> str:
    return f"{order.contact_first_name} {order.contact_last_name}".strip() or order.contact_email


def _money(cents, currency) -> dict:
    return {"amount_cents": cents, "currency": currency}


def _customer(order, kind, params, link=None) -> Target:
    return Target(CUSTOMER, kind, params, link or ("order", order.pk), user=order.user if order.user_id else None, email=order.contact_email)


def _store_ids(order) -> list:
    from orders.models import OrderItem

    return list(OrderItem.objects.filter(order=order).values_list("store_id", flat=True).distinct())


def order_placed(order, **_) -> list[Target]:
    params = {"order": order.order_number, "customer": _name(order), **_money(order.total_cents, order.currency)}
    return [
        _customer(order, "customer_order_placed", params),
        Target(STAFF, "staff_order_placed", {**params, "market": order.country_id}, ("order", order.pk), market_id=order.country_id,
               amount_cents=order.total_cents),
    ]


def order_paid(order, **_) -> list[Target]:
    from django.db.models import Q

    from orders.models import Order, OrderItem

    paid = [Order.OrderPaymentStatus.PAID, Order.OrderPaymentStatus.PARTIALLY_REFUNDED, Order.OrderPaymentStatus.REFUNDED]
    store_ids = _store_ids(order)
    seen_before = set(
        OrderItem.objects.filter(store_id__in=store_ids, order__payment_status__in=paid)
        .filter(Q(order__user_id=order.user_id) if order.user_id else Q(order__contact_email__iexact=order.contact_email))
        .exclude(order=order).values_list("store_id", flat=True)
    )
    params = {"order": order.order_number, "customer": _name(order)}
    link = ("order", order.pk)
    targets = [_customer(order, "customer_order_paid", {**params, **_money(order.total_cents, order.currency)})]
    for store_id in store_ids:
        targets.append(Target(SELLER, "order_received", params, link, store_id=store_id))
        targets.append(Target(SELLER, "payment_received", params, link, store_id=store_id, mail=False))
        if store_id not in seen_before:
            targets.append(Target(SELLER, "new_customer", params, link, store_id=store_id, mail=False))
    return targets


def order_cancelled(order, *, by: str, store=None, **_) -> list[Target]:
    """`by`: customer | seller | staff. A seller cancelling its own lines (`store`) tells the customer, not itself."""
    from orders.models import Order

    params = {"order": order.order_number, "customer": _name(order), "by": by, "store": store.display_name if store else ""}
    targets = [_customer(order, "customer_order_cancelled_store" if store else "customer_order_cancelled", params)]
    sellers_saw_it = order.payment_status in (Order.OrderPaymentStatus.PAID, Order.OrderPaymentStatus.PARTIALLY_REFUNDED, Order.OrderPaymentStatus.REFUNDED)
    if by in ("customer", "staff") and sellers_saw_it:            # sellers never saw an unpaid order, so there is nothing to retract
        targets += [Target(SELLER, f"order_cancelled_{by}", params, ("order", order.pk), store_id=sid) for sid in _store_ids(order)]
    if by != "staff":
        targets.append(Target(STAFF, "staff_order_cancelled", params, ("order", order.pk), market_id=order.country_id))
    return targets


def order_progress(kind):
    def build(order, *, store=None, **_) -> list[Target]:
        params = {"order": order.order_number, "customer": _name(order), "store": store.display_name if store else ""}
        return [_customer(order, f"{kind}_store" if store else kind, params)]
    return build


def _return_params(r) -> dict:
    # `reason` is the label in every language: it is rendered in the reader's language, not the buyer's.
    return {"order": r.order.order_number, "customer": _name(r.order), "reference": r.reference, "items": r.items.count(),
            "reason": r.reason.label or {"en": r.reason.code}}


def _return_customer(r, kind, params) -> Target:
    order = r.order
    return Target(CUSTOMER, kind, params, ("return", r.pk), user=order.user if order.user_id else None, email=order.contact_email)


def return_requested(r, *, due_days=None, **_) -> list[Target]:
    params = {**_return_params(r), "due_days": due_days or ""}
    link = ("return", r.pk)
    return [_return_customer(r, "customer_return_requested", params), Target(SELLER, "return_requested", params, link, store_id=r.store_id),
            Target(STAFF, "staff_return_requested", params, link, market_id=r.country_id)]


def return_cancelled(r, **_) -> list[Target]:
    return [Target(SELLER, "return_cancelled", _return_params(r), ("return", r.pk), store_id=r.store_id)]


def return_for_customer(kind):
    def build(r, **payload) -> list[Target]:
        return [_return_customer(r, kind, {**_return_params(r), **payload})]
    return build


def return_escalated(r, **_) -> list[Target]:
    params, link = _return_params(r), ("return", r.pk)
    return [Target(SELLER, "return_escalated", params, link, store_id=r.store_id), Target(STAFF, "staff_return_escalated", params, link, market_id=r.country_id)]


def return_decided(r, *, outcome: dict, **_) -> list[Target]:
    params = {**_return_params(r), "outcome": outcome}
    return [_return_customer(r, "customer_return_decided", params), Target(SELLER, "return_decided", params, ("return", r.pk), store_id=r.store_id)]


def refund_issued(r, **_) -> list[Target]:
    params, link = {**_return_params(r), **_money(r.refund_cents, r.currency)}, ("return", r.pk)
    return [_return_customer(r, "customer_refund_issued", params), Target(SELLER, "refund_issued", params, link, store_id=r.store_id),
            Target(STAFF, "staff_refund_issued", params, link, market_id=r.country_id, amount_cents=r.refund_cents)]


@dataclass(frozen=True)
class Spec:
    build: object                      # (subject, **payload) -> list[Target]
    subject_type: str
    market: object                     # subject -> country code


def _order_market(order):
    return order.country_id


def _return_market(r):
    return r.country_id


EVENTS: dict[str, Spec] = {
    "order.placed": Spec(order_placed, "order", _order_market),
    "order.paid": Spec(order_paid, "order", _order_market),
    "order.cancelled": Spec(order_cancelled, "order", _order_market),
    "order.shipped": Spec(order_progress("customer_order_shipped"), "order", _order_market),
    "order.out_for_delivery": Spec(order_progress("customer_order_out_for_delivery"), "order", _order_market),
    "order.delivered": Spec(order_progress("customer_order_delivered"), "order", _order_market),
    "return.requested": Spec(return_requested, "return", _return_market),
    "return.cancelled": Spec(return_cancelled, "return", _return_market),
    "return.approved": Spec(return_for_customer("customer_return_approved"), "return", _return_market),
    "return.rejected": Spec(return_for_customer("customer_return_rejected"), "return", _return_market),
    "return.no_refund": Spec(return_for_customer("customer_return_no_refund"), "return", _return_market),
    "return.escalated": Spec(return_escalated, "return", _return_market),
    "return.decided": Spec(return_decided, "return", _return_market),
    "refund.issued": Spec(refund_issued, "return", _return_market),
}

assert set(EVENTS) == set(DEFAULTS), set(EVENTS) ^ set(DEFAULTS)

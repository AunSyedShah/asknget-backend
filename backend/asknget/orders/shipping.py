"""The delivery rule: free from a threshold, otherwise a fee. Admin and seller both set the two numbers.

Settings (config registry, `shipping.*`): `free_over_cents` and `fee_cents` at store, market or global scope (the most
specific wins), and `rule_locked` at market or global scope: while on, store values are ignored and the admin's apply.

A delivery option whose price is empty follows the rule; one with a price is a fixed price and never touches it.
The rule is judged per seller, on that seller's items in the cart before any voucher.
"""

from dataclasses import dataclass, field

from django.db.models import Q

from config.models import Setting
from config.registry import definition

FREE_OVER, FEE, LOCKED = "shipping.free_over_cents", "shipping.fee_cents", "shipping.rule_locked"
_RANK = {"global": 1, "country": 2, "store": 3}


@dataclass(frozen=True)
class Rule:
    free_over_cents: int
    fee_cents: int
    locked: bool = False
    source: dict = field(default_factory=dict)   # {"free_over": "store" | "country" | "global" | "default", "fee": ...}

    def charge(self, store_subtotal_cents: int) -> tuple[int, bool]:
        """(price, waived) for an option that follows the rule."""
        if self.free_over_cents and store_subtotal_cents >= self.free_over_cents:
            return 0, True
        return self.fee_cents, False

    def remaining_cents(self, store_subtotal_cents: int) -> int:
        """How much more this seller's items must add up to for free delivery (0 when there is no threshold or it is met)."""
        return max(self.free_over_cents - store_subtotal_cents, 0) if self.free_over_cents else 0


def rules_for(store_ids, country_id) -> dict:
    """{store_id: Rule}, one query however many stores."""
    ids = list(store_ids)
    scope = Q(scope="global")
    if country_id:
        scope |= Q(scope="country", country_id=country_id)
    if ids:
        scope |= Q(scope="store", store_id__in=ids)
    rows = list(Setting.objects.filter(scope, key__in=(FREE_OVER, FEE, LOCKED)).values_list("key", "scope", "store_id", "value"))
    defaults = {key: definition(key).default for key in (FREE_OVER, FEE, LOCKED)}

    def pick(key, store_id, allow_store):
        best = (0, defaults[key], "default")
        for k, scope_name, row_store, value in rows:
            if k != key or (scope_name == "store" and (not allow_store or row_store != store_id)):
                continue
            if _RANK[scope_name] > best[0]:
                best = (_RANK[scope_name], value, scope_name)
        return best[1], best[2]

    out = {}
    for store_id in ids:
        locked, _ = pick(LOCKED, store_id, allow_store=False)
        free_over, free_src = pick(FREE_OVER, store_id, allow_store=not locked)
        fee, fee_src = pick(FEE, store_id, allow_store=not locked)
        out[store_id] = Rule(free_over, fee, bool(locked), {"free_over": free_src, "fee": fee_src})
    return out


def price_for(option, rule: Rule, store_subtotal_cents: int) -> tuple[int, bool, bool]:
    """(price, waived, follows_rule) for one option."""
    if option.price_cents is not None:
        return option.price_cents, False, False
    price, waived = rule.charge(store_subtotal_cents)
    return price, waived, True


# --------------------------------------------------------------------------- #
# A seller's own values (the seller portal reads and writes these)
# --------------------------------------------------------------------------- #

_OWN = {"free_over_cents": FREE_OVER, "fee_cents": FEE}


def store_rule(store) -> dict:
    """What applies to this store now, where each number comes from, whether the admin locked it, and the store's own overrides."""

    rule = rules_for([store.pk], store.country_id)[store.pk]
    own = dict(Setting.objects.filter(scope="store", store=store, key__in=_OWN.values()).values_list("key", "value"))
    return {
        "free_over_cents": rule.free_over_cents, "fee_cents": rule.fee_cents, "locked": rule.locked, "source": rule.source,
        "own": {name: own.get(key) for name, key in _OWN.items()},
        "currency": store.country.currency,
    }


def set_store_rule(store, user, values: dict) -> None:
    """Set the store's own threshold and/or fee (a missing key is left alone). 409 while the admin has locked the rule."""
    from ninja.errors import HttpError

    if rules_for([store.pk], store.country_id)[store.pk].locked:
        raise HttpError(409, "rule_locked")
    for name, value in values.items():
        try:
            definition(_OWN[name]).validate(value)
        except ValueError as exc:
            raise HttpError(422, f"{name} {exc}")
    existing = {row.key: row for row in Setting.objects.filter(scope="store", store=store, key__in=[_OWN[n] for n in values])}
    for name, value in values.items():
        row = existing.get(_OWN[name])
        if row is None:
            Setting.objects.create(key=_OWN[name], scope="store", store=store, value=value, updated_by=user)
        else:
            row.value, row.updated_by = value, user
            row.save(update_fields=["value", "updated_by", "updated_at"])


def clear_store_rule(store) -> None:
    """Back to the platform's numbers."""
    Setting.objects.filter(scope="store", store=store, key__in=_OWN.values()).delete()

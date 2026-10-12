"""Reading configuration. Nothing is cached: an admin edit applies on the next request,
the same promise the market settings make, and a read is a single indexed query."""

from dataclasses import dataclass
from datetime import datetime

from django.db.models import Q
from django.utils import timezone

from . import definitions  # noqa: F401  (make sure every setting is registered before any read)
from .models import CommissionRule, Setting
from .registry import REGISTRY, definition

_SCOPE_RANK = {"global": 1, "country": 2, "store": 3}


def _target(store, country):
    """(store_id, country_id) for a lookup. A store brings its own market; with neither
    given, fall back to the market of the current request (None outside a request)."""
    store_id = getattr(store, "pk", store)
    country_id = getattr(store, "country_id", None) or getattr(country, "code", country)
    if not country_id and not store_id:
        from markets.context import get_country

        current = get_country()
        country_id = current.code if current else None
    return store_id, country_id


def resolve(keys, *, store=None, country=None) -> dict:
    """{key: (value, source)} where source is "store", "country", "global" or "default"."""
    keys = list(keys)
    defs = {key: definition(key) for key in keys}            # unknown key → UnknownSetting
    store_id, country_id = _target(store, country)

    scope_filter = Q(scope="global")
    if country_id:
        scope_filter |= Q(scope="country", country_id=country_id)
    if store_id:
        scope_filter |= Q(scope="store", store_id=store_id)

    best: dict[str, tuple[int, object, str]] = {}
    for key, scope, value in Setting.objects.filter(scope_filter, key__in=keys).values_list("key", "scope", "value"):
        rank = _SCOPE_RANK[scope]
        if key not in best or rank > best[key][0]:
            best[key] = (rank, value, scope)
    return {key: (best[key][1], best[key][2]) if key in best else (defs[key].default, "default") for key in keys}


def get_many(keys, *, store=None, country=None) -> dict:
    return {key: value for key, (value, _source) in resolve(keys, store=store, country=country).items()}


def get(key, *, store=None, country=None):
    return resolve([key], store=store, country=country)[key][0]


# --------------------------------------------------------------------------------------
# Commission
# --------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Rate:
    bps: int
    rule_id: object   # the CommissionRule that decided it; None when no rule applied

    @property
    def percent(self) -> float:
        return self.bps / 100


NO_RATE = Rate(0, None)


def _category_chains(category_ids) -> dict:
    """{category_id: [itself, parent, grandparent, ...]}, loading each level in one query."""
    from catalog.models import Category

    wanted = {cid for cid in category_ids if cid}
    parent_of: dict = {}
    pending = set(wanted)
    while pending:
        rows = dict(Category.objects.filter(pk__in=pending).values_list("pk", "parent_id"))
        parent_of.update(rows)
        pending = {p for p in rows.values() if p and p not in parent_of}
    chains = {}
    for cid in wanted:
        chain, seen, current = [cid], {cid}, parent_of.get(cid)
        while current and current not in seen:
            chain.append(current)
            seen.add(current)
            current = parent_of.get(current)
        chains[cid] = chain
    return chains


def commission_rates(store, category_ids, *, at: datetime | None = None) -> dict:
    """The commission rate that applies to each category for this store, as {category_id: Rate}.

    Most specific rule first: the store's own, then the category (the nearest parent that has
    one counts too), then the store's market, then global. Within one level the latest rule
    that has already begun wins. With no rule at all the rate is 0 and `rule_id` is None —
    a visible gap, not a guess.
    """
    at = at or timezone.now()
    category_ids = list(category_ids)
    chains = _category_chains(category_ids)
    in_chains = {cid for chain in chains.values() for cid in chain}

    scope = Q(scope="global") | Q(scope="country", country_id=store.country_id) | Q(scope="store", store_id=store.pk)
    if in_chains:
        scope |= Q(scope="category", category_id__in=in_chains)
    rules = list(
        CommissionRule.objects.filter(scope, is_active=True, effective_from__lte=at)
        .values("pk", "scope", "category_id", "rate_bps", "effective_from")
    )

    def best(chain):
        winner, winner_key = None, None
        for rule in rules:
            if rule["scope"] == "category":
                if rule["category_id"] not in chain:
                    continue
                level = (3, len(chain) - chain.index(rule["category_id"]))   # closer to the product's own category wins
            else:
                level = ({"global": 1, "country": 2, "store": 4}[rule["scope"]], 0)
            key = (*level, rule["effective_from"])
            if winner_key is None or key > winner_key:
                winner, winner_key = rule, key
        return Rate(winner["rate_bps"], winner["pk"]) if winner else NO_RATE

    return {cid: best(chains.get(cid, [])) for cid in category_ids}

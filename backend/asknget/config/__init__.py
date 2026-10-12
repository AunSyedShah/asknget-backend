"""Admin-managed configuration.

    from config import services as config
    config.get("payouts.hold_days", store=store)
    config.commission_rates(store, [category_id, ...])

Kept free of imports on purpose: Django loads this package while it is still building
the app registry, so importing models here would fail.
"""

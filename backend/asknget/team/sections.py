"""The vocabulary of access: the eight sections of the seller portal and three levels in each.

The design's Member Details page shows a level per section (View / View + Manage) and its
Edit Permissions popup toggles them, so one model serves both: `none`, `view`, `manage`.
`manage` implies `view`. The store owner is not a member row; they always have `manage`
everywhere, plus the two things no section covers (team management, plan changes).
"""

SECTIONS = ("dashboard", "orders", "products", "inventory", "customers", "marketing", "analytics", "transactions")
LEVELS = ("none", "view", "manage")
_ORDER = {level: rank for rank, level in enumerate(LEVELS)}

SECTION_LABELS = {
    "dashboard": "Dashboard", "orders": "Orders", "products": "Products", "inventory": "Inventory",
    "customers": "Customers", "marketing": "Marketing", "analytics": "Analytics", "transactions": "Transactions",
}


def normalise(permissions) -> dict:
    """A complete {section: level} map; a missing section is `none`. Raises ValueError on a
    section or level that does not exist, so a typo cannot silently grant or deny access."""
    permissions = permissions or {}
    unknown = set(permissions) - set(SECTIONS)
    if unknown:
        raise ValueError(f"unknown section(s): {', '.join(sorted(unknown))}")
    bad = {section: level for section, level in permissions.items() if level not in LEVELS}
    if bad:
        raise ValueError("levels must be none, view or manage: " + ", ".join(f"{s}={v!r}" for s, v in sorted(bad.items())))
    return {section: permissions.get(section, "none") for section in SECTIONS}


def required_level(method: str) -> str:
    """Reading needs `view`; anything that changes state needs `manage`."""
    return "view" if method.upper() in ("GET", "HEAD", "OPTIONS") else "manage"


class Access:
    """What one person may do in one store."""

    def __init__(self, permissions: dict, *, is_owner: bool = False):
        self.permissions = normalise(permissions)
        self.is_owner = is_owner

    def level(self, section: str) -> str:
        return self.permissions[section]

    def allows(self, section: str, needed: str = "view") -> bool:
        return _ORDER[self.permissions[section]] >= _ORDER[needed]

    def sections(self, minimum: str = "view") -> list[str]:
        return [section for section in SECTIONS if self.allows(section, minimum)]


OWNER_ACCESS = Access({section: "manage" for section in SECTIONS}, is_owner=True)

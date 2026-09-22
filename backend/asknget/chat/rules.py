"""Chat policy as data — change a table, run the tests, ship.

`CONTACT_PATTERNS` catches attempts to move the conversation off the marketplace
(the usual abuse in vendor chat). Matches are **flagged for staff, never blocked**:
false positives are common ("call me on delivery, flat 3B") and silently eating a
message is worse than reviewing one.
"""

import re

MAX_BODY = 4000
PREVIEW_CHARS = 140

# Messages per user per window — counted in SQL, not in the cache: LocMem is
# per-process and gunicorn runs three of them.
RATE_LIMIT_MESSAGES = 30
RATE_LIMIT_SECONDS = 60

CONTACT_PATTERNS: tuple[tuple[str, re.Pattern], ...] = (
    ("email", re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")),
    ("phone", re.compile(r"(?<!\w)(?:\+?\d[\s-]?){9,15}(?!\w)")),
    ("link", re.compile(r"\b(?:https?://|www\.)\S+", re.I)),
    ("off_platform", re.compile(r"\b(whats\s?app|telegram|instagram|facebook|wechat)\b", re.I)),
)


def flag_reason(body: str) -> str:
    """The first pattern the body trips, or "" when it looks fine."""
    for name, pattern in CONTACT_PATTERNS:
        if pattern.search(body):
            return name
    return ""


def preview(body: str) -> str:
    text = " ".join(body.split())
    return text[:PREVIEW_CHARS]

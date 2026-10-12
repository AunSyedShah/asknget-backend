"""The catalogue of tunable settings: what exists, its type, bounds and default.

Definitions live in code (`config.definitions`) so a typo cannot create a setting
nobody reads, and so an empty database is a fully working system. Values live in
the database (`config.Setting`) so an admin changes them without a release.
"""

from dataclasses import dataclass
from typing import Any

SCOPES = ("global", "country", "store")


class UnknownSetting(KeyError):
    """A key no one registered. Raised on read as well as on write: a misspelt key
    in code should fail loudly, not silently return a default."""


@dataclass(frozen=True)
class Definition:
    key: str
    type: str                      # "int" | "bool" | "str" | "enum"
    default: Any
    label: str
    help: str = ""
    scopes: tuple[str, ...] = SCOPES
    min: int | None = None
    max: int | None = None
    choices: tuple[str, ...] = ()

    def validate(self, value: Any) -> Any:
        """Return the value if it satisfies this definition, else raise ValueError."""
        if self.type == "bool":
            if not isinstance(value, bool):
                raise ValueError("must be true or false")
        elif self.type == "int":
            # bool is an int subclass in Python; "true" must not pass as 1
            if isinstance(value, bool) or not isinstance(value, int):
                raise ValueError("must be a whole number")
            if self.min is not None and value < self.min:
                raise ValueError(f"must be at least {self.min}")
            if self.max is not None and value > self.max:
                raise ValueError(f"must be at most {self.max}")
        elif self.type == "enum":
            if value not in self.choices:
                raise ValueError("must be one of: " + ", ".join(self.choices))
        elif self.type == "str":
            if not isinstance(value, str):
                raise ValueError("must be text")
        else:                                           # a bad definition, caught at import
            raise ValueError(f"unknown setting type {self.type!r}")
        return value


REGISTRY: dict[str, Definition] = {}


def register(key: str, type: str, default: Any, label: str, **extra) -> Definition:
    if key in REGISTRY:
        raise ValueError(f"setting {key!r} is registered twice")
    definition = Definition(key=key, type=type, default=default, label=label, **extra)
    definition.validate(default)                        # a default that breaks its own rules is a bug
    unknown = set(definition.scopes) - set(SCOPES)
    if unknown:
        raise ValueError(f"setting {key!r}: unknown scope(s) {sorted(unknown)}")
    REGISTRY[key] = definition
    return definition


def definition(key: str) -> Definition:
    try:
        return REGISTRY[key]
    except KeyError:
        raise UnknownSetting(key) from None

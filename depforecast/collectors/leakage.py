"""Leakage guard: feature queries must never look at data from after T0 (SPEC §5.5)."""

import re
from datetime import date, datetime

# Fields that describe the present, not the repo as it was at T0.
FORBIDDEN_FIELDS = (
    "stargazerCount",
    "stargazers",
    "forkCount",
    "forks",
    "watchers",
    "pushedAt",
    "updatedAt",
    "diskUsage",
)
# Search qualifiers that mean "as of now" (e.g. currently open issues).
FORBIDDEN_QUALIFIERS = ("is:open", "state:open", "is:closed", "state:closed")
# Connections filtered by current state, e.g. issues(states: OPEN).
_CURRENT_STATE = re.compile(r"\b(?:issues|pullRequests)\s*\([^)]*states\s*:", re.IGNORECASE)

_TIMESTAMP = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z")
# A plain date (search qualifiers) and what comes right before it.
_PLAIN_DATE = re.compile(r"(?P<prefix><=|>=|<|>|\.\.|:)?(?P<date>\d{4}-\d{2}-\d{2})(?!T)")


class LeakageError(Exception):
    """A feature query would read data from after its as_of date."""


def assert_no_leakage(query: str, as_of: datetime) -> None:
    """Raise LeakageError if a feature query references the future or present-only data."""
    for field in FORBIDDEN_FIELDS:
        if re.search(rf"\b{field}\b", query):
            raise LeakageError(f"feature query uses present-only field {field!r}")
    lowered = query.lower()
    for qualifier in FORBIDDEN_QUALIFIERS:
        if qualifier in lowered:
            raise LeakageError(f"feature query uses present-only search {qualifier!r}")
    if _CURRENT_STATE.search(query):
        raise LeakageError("feature query filters issues/PRs by their current state")

    for stamp in _TIMESTAMP.findall(query):
        when = datetime.fromisoformat(stamp)
        if when > as_of:
            raise LeakageError(f"feature query reads up to {stamp}, after as_of {as_of:%Y-%m-%d}")

    cutoff: date = as_of.date()
    for m in _PLAIN_DATE.finditer(query):
        day = date.fromisoformat(m["date"])
        # "<D" excludes day D, so D == as_of is fine. Any other plain date includes
        # the whole day (e.g. the inclusive end of "A..B"), so it must be before as_of.
        ok = day <= cutoff if m["prefix"] == "<" else day < cutoff
        if not ok:
            raise LeakageError(f"feature query covers {day}, not before as_of {cutoff}")

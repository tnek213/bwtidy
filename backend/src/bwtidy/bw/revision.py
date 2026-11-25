"""When two revisionDates name the same revision of an item (docs/spec.md, Writes)."""

import re
from datetime import datetime
from typing import Any

## a rounded and a truncated spelling of one instant are up to 1 ms apart
TOLERANCE_NS = 1_000_000
NS_PER_SECOND = 1_000_000_000
FRACTION_DIGITS = 9

INSTANT = re.compile(
    r"(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})(?:\.(\d+))?(Z|[+-]\d{2}:\d{2})", re.ASCII
)


def instant(value: Any) -> int | None:
    """The UTC instant a revisionDate names, in nanoseconds since the epoch, None for a value
    that is no ISO 8601 timestamp with a zone. Digits beyond the nanosecond are dropped."""
    if not isinstance(value, str):
        return None
    match = INSTANT.fullmatch(value)
    if match is None:
        return None
    whole, fraction, zone = match.groups()
    try:
        moment = datetime.fromisoformat(whole + ("+00:00" if zone == "Z" else zone))
    except ValueError:
        return None
    digits = (fraction or "")[:FRACTION_DIGITS].ljust(FRACTION_DIGITS, "0")
    return int(moment.timestamp()) * NS_PER_SECOND + int(digits)


def same_revision(a: Any, b: Any) -> bool:
    """Whether two revisionDates name the same revision: equal, or both timestamps at most
    1 ms apart. None or a value that does not parse is the same only as the identical value.

    The server keeps a revisionDate with more digits than the 3 of bw serve's answers
    (bitwarden.com 7, Vaultwarden 6), and one instant can reach bwtidy in two spellings, one
    with the digits cut off and one with them rounded (.337Z in one answer, .336Z in the
    next). A cut and a rounded spelling with 3 digits each are up to exactly 1 ms apart, so
    1 ms still counts as the same. Every write gives an item a new revisionDate, and no two
    writes of one item are that close.
    """
    if a == b:
        return True
    first, second = instant(a), instant(b)
    if first is None or second is None:
        return False
    return abs(first - second) <= TOLERANCE_NS


class Spellings:
    """The spelling of each item's revisionDate bw serve answered first, kept while bw serve
    answers the same revision in another spelling (same_revision).

    So everything that reads the vault (the cache, a backup's listing, the write path's GET
    and PUT answers) sees one spelling of a revision, and a backup's fingerprint, which
    hashes the revisionDates, stays equal to the vault's. Item ids and revisionDates only,
    never a value of an item.
    """

    def __init__(self) -> None:
        self.known: dict[str, str] = {}

    def settle(self, item: Any) -> None:
        """Respell item's revisionDate in place as the known one when it is the same
        revision, else remember it as the item's spelling."""
        if not isinstance(item, dict):
            return
        item_id, revision = item.get("id"), item.get("revisionDate")
        if not isinstance(item_id, str) or not isinstance(revision, str):
            return
        known = self.known.get(item_id)
        if known is not None and same_revision(known, revision):
            item["revisionDate"] = known
        else:
            self.known[item_id] = revision

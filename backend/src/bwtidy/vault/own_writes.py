"""The chain of bwtidy's own writes in a session, so they never conflict with themselves."""

from typing import Any

from bwtidy.bw import same_revision
from bwtidy.operations import COLLECTIONS, FAVORITE, FOLDER, shown_now

## how many of bwtidy's own writes a revisionDate check follows, more is a loop
OWN_WRITES_CHAIN = 1000

type Partial = dict[str, Any]

PARTIAL_COLUMNS = (FOLDER, FAVORITE, COLLECTIONS)


def partial(item: dict[str, Any]) -> Partial:
    """The values of an item another client changes without a new revisionDate (see
    operations.shown): its folder, favorite flag and collections."""
    return {column: shown_now(item, column) for column in PARTIAL_COLUMNS}


class OwnWrites:
    """What the write path wrote this session, followed by the checks of later writes.

    revisions remembers, for every item the write path wrote this session, the revisionDate
    before each write and the one bw serve answered after it (docs/spec.md, Writes). A check
    of an edit based on a "before" is made against the "after" instead, following the chain,
    so an edit queued while bwtidy committed never conflicts with bwtidy's own commit. Only
    revisionDates from the write path's own PUT answers ever go in here. A revisionDate is
    looked up in any spelling of its instant (revision.same_revision).

    partials remembers, for the same writes, the folder, favorite flag and collections the
    write found on the item and the ones it left, in write order. They can change without a
    new revisionDate, so a value the grid showed is followed through them (shown_holds).

    conversions remembers, for every conversion the write path made this session (convertItem,
    moveToPersonal, an applyTemplate that changed the type), the new item's id by the old one,
    so a queued change of the tool note's sections that names the old id lands on the new one
    (docs/spec.md, The tool note).
    """

    def __init__(self) -> None:
        ## per item, the (before, after) of each write in write order
        self.revisions: dict[str, list[tuple[str, str]]] = {}
        self.partials: dict[str, list[tuple[Partial, Partial]]] = {}
        self.conversions: dict[str, str] = {}

    def clear(self) -> None:
        self.revisions = {}
        self.partials = {}
        self.conversions = {}

    def record_write(self, item_id: str, before: str | None, after: str | None) -> None:
        if before and after and not same_revision(before, after):
            self.revisions.setdefault(item_id, []).append((before, after))

    def _after(self, item_id: str, revision: Any) -> str | None:
        """The revisionDate the latest own write found as revision (in any spelling, see
        revision.same_revision) left, None when no own write found it."""
        for before, after in reversed(self.revisions.get(item_id, [])):
            if same_revision(before, revision):
                return after
        return None

    def written_over(self, item_id: str, revision: Any) -> bool:
        """Whether one of bwtidy's own writes turned this revisionDate of the item into a
        later one."""
        return isinstance(revision, str) and self._after(item_id, revision) is not None

    def current_revision(self, item_id: str, expected: str) -> str:
        """The revisionDate bwtidy's own writes turned expected into, else expected itself."""
        for _ in range(OWN_WRITES_CHAIN):
            after = self._after(item_id, expected)
            if after is None:
                break
            expected = after
        return expected

    def record_partial(self, item_id: str, before: Partial | None, after: Partial | None) -> None:
        if before is None or after is None:
            return
        writes = self.partials.setdefault(item_id, [])
        writes.append((before, after))
        del writes[:-OWN_WRITES_CHAIN]

    def shown_holds(self, item_id: str, column: str, shown: Any, actual: Any) -> bool:
        """Whether a value the grid showed in a column of operations.shown still holds.

        It does when it is the item's value now, or when bwtidy's own writes turned it into
        that: from one of them that found the shown value, each later one found what the one
        before it left and the last one left the value now. Any other change was made
        elsewhere. A column the writes do not remember (the URIs of a setUris) holds only when
        it is the value now: a whole new list would undo bwtidy's own write of it.
        """
        if shown == actual:
            return True
        if column not in PARTIAL_COLUMNS:
            return False
        writes = self.partials.get(item_id, [])
        for start, (found, _) in enumerate(writes):
            if found[column] != shown:
                continue
            value = shown
            for before, after in writes[start:]:
                if before[column] != value:
                    break
                value = after[column]
            else:
                if value == actual:
                    return True
        return False

    def record_conversion(self, old_id: str, new_id: str) -> None:
        if old_id and new_id and old_id != new_id:
            self.conversions[old_id] = new_id

    def converted(self, item_id: str) -> str:
        """The id of the item a conversion of this session made of item_id, following later
        conversions of that one too. item_id itself when none did."""
        for _ in range(OWN_WRITES_CHAIN):
            new_id = self.conversions.get(item_id)
            if new_id is None:
                break
            item_id = new_id
        return item_id

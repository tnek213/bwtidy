"""How hard the datatype checks work (docs/spec.md, Statistics), so the engine's limits can be tuned
from real use.

The numbers live in the memory of this process and go with the session. They hold item names,
columns, lengths and counts, never a character of a value, and nothing here is logged. Only
GET /api/statistics reads them, no MCP tool does.
"""

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from bwtidy.items.patterns import (
    MAX_DEPTH,
    MAX_PATTERN_LEN,
    MAX_REPEAT,
    MAX_STATES,
    MAX_VALUE_LEN,
    CompiledPattern,
    FormatResult,
)
from bwtidy.sections.datatypes import Datatype

MAX_ROWS = 500
MS_PER_SECOND = 1000
MS_DECIMALS = 3


def _ms(seconds: float) -> float:
    return round(seconds * MS_PER_SECOND, MS_DECIMALS)


@dataclass(frozen=True)
class Measure:
    """One value scored: its NFC length, the seconds it took, its result (None when too long)."""

    length: int
    seconds: float
    result: FormatResult | None


@dataclass(frozen=True)
class Row:
    """The last scoring of one (item, column, datatype). A column with several values (login.uris)
    has one row: the longest length, the distance that counts, the most copies per counted
    repetition, and steps and seconds summed."""

    item_id: str
    item: str
    column: str
    datatype: str
    states: int
    max_repeat: int
    shortest: int
    length: int
    distance: int | None
    repeats_used: tuple[int, ...]
    steps: int
    seconds: float
    too_long: bool

    def json(self) -> dict[str, Any]:
        return {
            "itemId": self.item_id,
            "item": self.item,
            "column": self.column,
            "datatype": self.datatype,
            "states": self.states,
            "maxRepeat": self.max_repeat,
            "shortest": self.shortest,
            "length": self.length,
            "distance": self.distance,
            "repeatsUsed": list(self.repeats_used),
            "steps": self.steps,
            "ms": _ms(self.seconds),
            "tooLong": self.too_long,
        }


@dataclass
class _Counters:
    """One datatype since the login, or since its pattern last changed."""

    name: str
    pattern: CompiledPattern
    values_scored: int = 0
    values_too_long: int = 0
    slowest_seconds: float = 0.0
    last_reload_seconds: float = 0.0

    def json(self, datatype_id: str) -> dict[str, Any]:
        return {
            "id": datatype_id,
            "name": self.name,
            "states": self.pattern.states,
            "maxRepeat": self.pattern.max_repeat,
            "shortest": self.pattern.shortest,
            "valuesScored": self.values_scored,
            "valuesTooLong": self.values_too_long,
            "slowestMs": _ms(self.slowest_seconds),
            "lastReloadMs": _ms(self.last_reload_seconds),
        }


def _most_copies(results: list[FormatResult]) -> tuple[int, ...]:
    return tuple(max(counts) for counts in zip(*(r.repeats_used for r in results), strict=True))


class Statistics:
    """The collector the scoring writes into, one per process (it sits on the VaultCache)."""

    def __init__(self) -> None:
        self.reloads = 0
        self._rows: dict[tuple[str, str, str], Row] = {}
        self._counters: dict[str, _Counters] = {}

    def clear(self) -> None:
        self.reloads = 0
        self._rows = {}
        self._counters = {}

    def start_reload(self, datatypes: Iterable[Datatype]) -> None:
        """A reload scores every value again, so the rows start over. The counters go on, except
        for a datatype that went away or got another pattern: they described something else."""
        self.reloads += 1
        self._rows = {}
        counters: dict[str, _Counters] = {}
        for datatype in datatypes:
            if datatype.compiled is None:
                continue
            kept = self._counters.get(datatype.id)
            if kept is not None and self._same_pattern(kept.pattern, datatype.compiled):
                kept.name = datatype.name
                kept.last_reload_seconds = 0.0
                counters[datatype.id] = kept
            else:
                counters[datatype.id] = _Counters(datatype.name, datatype.compiled)
        self._counters = counters

    @staticmethod
    def _same_pattern(one: CompiledPattern, other: CompiledPattern) -> bool:
        return (one.pattern, one.ignore_case, one.whole_value) == (
            other.pattern,
            other.ignore_case,
            other.whole_value,
        )

    def has_row(self, item_id: str, column: str, datatype_id: str) -> bool:
        return (item_id, column, datatype_id) in self._rows

    def record(
        self,
        item_id: str,
        item_name: str,
        column: str,
        datatype: Datatype,
        pattern: CompiledPattern,
        measures: list[Measure],
        lowest: FormatResult | None,
    ) -> None:
        """One scoring of a coordinate's values, lowest being the result that counts."""
        results = [m.result for m in measures if m.result is not None]
        seconds = sum(m.seconds for m in measures)
        self._rows[(item_id, column, datatype.id)] = Row(
            item_id=item_id,
            item=item_name,
            column=column,
            datatype=datatype.name,
            states=pattern.states,
            max_repeat=pattern.max_repeat,
            shortest=pattern.shortest,
            length=max(m.length for m in measures),
            distance=lowest.distance if lowest else None,
            repeats_used=_most_copies(results),
            steps=sum(r.steps for r in results),
            seconds=seconds,
            too_long=len(results) < len(measures),
        )
        counters = self._counters.setdefault(datatype.id, _Counters(datatype.name, pattern))
        counters.values_scored += len(measures)
        counters.values_too_long += len(measures) - len(results)
        counters.slowest_seconds = max(counters.slowest_seconds, max(m.seconds for m in measures))
        counters.last_reload_seconds += seconds

    def answer(self) -> dict[str, Any]:
        """GET /api/statistics: the limits, the datatypes and the slowest rows first."""
        rows = sorted(self._rows.values(), key=lambda row: row.seconds, reverse=True)
        return {
            "limits": {
                "maxPatternLen": MAX_PATTERN_LEN,
                "maxRepeat": MAX_REPEAT,
                "maxStates": MAX_STATES,
                "maxDepth": MAX_DEPTH,
                "maxValueLen": MAX_VALUE_LEN,
            },
            "reloads": self.reloads,
            "datatypes": [c.json(id_) for id_, c in self._counters.items()],
            "rows": [row.json() for row in rows[:MAX_ROWS]],
            "rowCount": len(rows),
        }

"""Templates (docs/spec.md, Templates) matched by duck typing. The template model, its parser and
its changes are in sections/templates.py.

A template is a name, an item type and a list of coordinates. An item matches when it has the
template's type and a non-empty value for every required coordinate (one not optional). Among
several matches the one with most required coordinates wins, then the one with most format
terms, then the one with most optional coordinates filled (the first in the list on a tie). A
field name pattern is one coordinate however many fields fit it: the item's custom fields that
no exact coordinate of the template names and no pattern before it took. The
fill score is the share of a template's required coordinates an item has filled, whatever its
type, and it names the required coordinates the item lacks. Templates that score alike (same type
and same coordinates, datatypes and optional flags) never differ: duplicate_warnings names them.

A coordinate can name a datatype (docs/spec.md, Datatypes). It adds a term of 0 to 1 to the fill
score: how well the value matches its format. An optional coordinate adds one only when the item
has a value for it. This module scores items it is handed. The templates and datatypes of the
tool note, and the results kept by item id, column and the datatype's pattern and flags (never a
character of a value), are in vault/formats.py.
"""

import math
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from typing import Any

from bwtidy.items.coordinates import (
    BOOLEAN_FIELD,
    HIDDEN_FIELD,
    Coordinate,
    Lookups,
    Value,
    field_types,
    flatten,
    is_secret,
)
from bwtidy.items.patterns import CompiledPattern, FormatResult, ValueTooLong
from bwtidy.sections.datatypes import Datatype
from bwtidy.sections.templates import (
    AnyCoordinate,
    FieldCoordinate,
    PathCoordinate,
    Template,
    column_of,
    coordinate_of,
    folded_name,
    is_exact,
)


def find_template(templates: list[Template], name: str) -> Template | None:
    return next((t for t in templates if t.name == name), None)


def datatype_warnings(templates: list[Template], datatypes: list[Datatype]) -> list[str]:
    """What is wrong with the datatypes the templates name: each of those counts as none."""
    by_id = {d.id: d for d in datatypes}
    warnings = []
    for template in templates:
        for coordinate in template.coordinates:
            if coordinate.datatype is None:
                continue
            column = column_of(coordinate)
            if not is_exact(coordinate):
                column = f"the pattern {column}"
            datatype = by_id.get(coordinate.datatype)
            if datatype is None:
                warnings.append(
                    f"{template.name}: {column} names the unknown datatype"
                    f" {coordinate.datatype!r}, it counts as no datatype"
                )
            elif datatype.broken is not None:
                warnings.append(
                    f"{template.name}: the datatype {datatype.name!r} of {column} is broken,"
                    " it counts as no datatype"
                )
    return warnings


def duplicate_warnings(templates: list[Template], datatypes: list[Datatype]) -> list[str]:
    """A warning for each template that scores like one listed before it: the same type and the
    same set of (column, datatype, optional). The first of them takes every match. An unknown or
    broken datatype counts as none, as in scoring. Order and field types do not change a score.
    A pattern compares by its text ignoring case, never equal to a field of that name."""
    usable = {d.id for d in datatypes if d.broken is None}
    first: dict[tuple[int, frozenset[tuple[str, str, str | None, bool]]], str] = {}
    warnings = []
    for template in templates:
        key = (
            template.type,
            frozenset(
                (
                    c.kind,
                    column_of(c) if is_exact(c) else folded_name(c.pattern),
                    c.datatype if c.datatype in usable else None,
                    bool(c.optional),
                )
                for c in template.coordinates
            ),
        )
        earlier = first.setdefault(key, template.name)
        if earlier != template.name:
            warnings.append(
                f"{template.name}: same type and fields as {earlier}, which is listed first"
                " and takes every match."
            )
    return warnings


def format_of(compiled: CompiledPattern, value: str) -> FormatResult | None:
    """How the value matches. None when it is too long to check."""
    try:
        return compiled.score(value)
    except ValueTooLong:
        return None


def lowest_of(results: list[FormatResult | None]) -> FormatResult | None:
    """The lowest result, a value too long to check (None) being the lowest of all."""
    if all(r is not None for r in results):
        return min((r for r in results if r is not None), key=lambda r: r.score)
    return None


def floored_hundredths(score: float) -> int:
    """The score in whole hundredths, down, so a partial value never counts as a whole point."""
    ## rounded first as 1 - 71/100 is a hair below 0.29 in binary, and a real fraction is never
    ## this close to a hundredth as a value has at most MAX_VALUE_LEN characters
    return math.floor(round(score * 100, 6))


@dataclass(frozen=True)
class FormatScore:
    """How the value of one coordinate matches its datatype. Holds no character of the value.

    score is floored to two decimals. distance, firstEdit and expected are None without a
    value, for a value too long to check (tooLong) and for a boolean field.
    """

    column: str
    datatype: str
    score: float
    distance: int | None = None
    firstEdit: int | None = None
    expected: str | None = None
    tooLong: bool = False


@dataclass(frozen=True)
class Score:
    template: str
    score: float
    typeMatches: bool
    ## a float only when a format term is partial, a whole number is an int
    filled: float
    total: int
    ## the display strings (grid column ids) of the coordinates the item has no value for
    missing: tuple[str, ...]
    formats: tuple[FormatScore, ...] = ()
    ## the optional coordinates the item has a value for, the last tie breaker among matches
    optionalFilled: int = 0

    @property
    def full(self) -> bool:
        return self.typeMatches and self.filled == self.total


## how a coordinate's texts match a datatype, given the item and the column they are of: the
## lowest result over the texts (see lowest_of)
type ResultOf = Callable[[dict[str, Any], str, list[str], Datatype], FormatResult | None]


def computed(
    item: dict[str, Any], column: str, texts: list[str], datatype: Datatype
) -> FormatResult | None:
    """The result of the texts, computed every time and kept nowhere."""
    compiled = datatype.compiled
    assert compiled is not None
    return lowest_of([format_of(compiled, text) for text in texts])


@dataclass(frozen=True)
class Scoring:
    """What scoring knows of the datatypes: the ones that are not broken, how the result of a
    value is found, and whether the answer is for MCP."""

    by_id: dict[str, Datatype] = field(default_factory=dict)
    result_of: ResultOf = computed
    for_mcp: bool = False


def scoring(
    datatypes: Iterable[Datatype], result_of: ResultOf = computed, for_mcp: bool = False
) -> Scoring:
    usable = {d.id: d for d in datatypes if d.compiled is not None}
    return Scoring(usable, result_of, for_mcp)


def _format_score(
    item: dict[str, Any],
    coordinate: Coordinate,
    field_type: int | None,
    value: Value | None,
    datatype: Datatype,
    formats: Scoring,
) -> FormatScore:
    texts: list[str] = []
    if field_type != BOOLEAN_FIELD:
        listed = value if isinstance(value, list) else [value]
        texts = [text for text in listed if isinstance(text, str)]
    column = coordinate.display
    if not texts:
        return FormatScore(column=column, datatype=datatype.name, score=0)
    result = formats.result_of(item, column, texts, datatype)
    if result is None:
        return FormatScore(column=column, datatype=datatype.name, score=0, tooLong=True)
    return FormatScore(
        column=column,
        datatype=datatype.name,
        score=floored_hundredths(result.score) / 100,
        distance=result.distance,
        firstEdit=result.first_edit,
        expected=result.expected,
    )


## per template coordinate, the item's coordinates with a value that belong to it
type Resolved = list[tuple[AnyCoordinate, list[Coordinate]]]


def resolve(template: Template, values: dict[Coordinate, Value]) -> Resolved:
    """Each coordinate of the template with the item's coordinates that have a value for it.

    An exact coordinate has its own column or none. A pattern has the custom fields whose name
    fits it, less those an exact field coordinate of the template names and those a pattern
    listed before it took.
    """
    named = {folded_name(c.name) for c in template.coordinates if isinstance(c, FieldCoordinate)}
    free = [c for c in values if c.kind == "field" and folded_name(c.name) not in named]
    taken: set[Coordinate] = set()
    resolved: Resolved = []
    for declared in template.coordinates:
        if is_exact(declared):
            coordinate = coordinate_of(declared)
            resolved.append((declared, [coordinate] if coordinate in values else []))
            continue
        found = [c for c in free if c not in taken and declared.matches(c.name)]
        taken.update(found)
        resolved.append((declared, found))
    return resolved


def _format_terms(
    item: dict[str, Any], resolved: Resolved, values: dict[Coordinate, Value], formats: Scoring
) -> tuple[int, int, tuple[FormatScore, ...]]:
    """How many format terms the template has, their sum in hundredths and the formats to show.

    For MCP a secret column's term is whether it is set and it has no entry, so a pattern can
    never be an oracle on a secret. A secret by the item's field type, or by the template's when
    the item has no such field. An optional coordinate without a value has no term. A field name
    pattern's term is the lowest of the fields it has, and its entry is that field's.
    """
    if not formats.by_id:
        return 0, 0, ()
    terms = hundredths = 0
    entries: list[FormatScore] = []
    types = field_types(item)
    for declared, found in resolved:
        datatype = formats.by_id.get(declared.datatype) if declared.datatype else None
        if datatype is None:
            continue
        if declared.optional and not found:
            continue
        terms += 1
        declared_type = None if isinstance(declared, PathCoordinate) else declared.fieldType
        if not found:
            ## a required coordinate without a value: its own column, or the pattern's
            coordinate = coordinate_of(declared) if is_exact(declared) else None
            secret = (
                is_secret(coordinate, types.get(coordinate, declared_type))
                if coordinate is not None
                else declared_type == HIDDEN_FIELD
            )
            if formats.for_mcp and secret:
                continue
            column = column_of(declared)
            entries.append(FormatScore(column=column, datatype=datatype.name, score=0))
            continue
        scored: list[FormatScore] = []
        for coordinate in found:
            field_type = types.get(coordinate, declared_type)
            if formats.for_mcp and is_secret(coordinate, field_type):
                continue
            value = values[coordinate]
            scored.append(_format_score(item, coordinate, field_type, value, datatype, formats))
        if not scored:
            ## every value is secret and set, so for MCP the term is whole
            hundredths += 100
            continue
        lowest = min(scored, key=lambda entry: entry.score)
        hundredths += round(lowest.score * 100)
        entries.append(lowest)
    return terms, hundredths, tuple(entries)


def _score(
    item: dict[str, Any], template: Template, values: dict[Coordinate, Value], formats: Scoring
) -> Score:
    resolved = resolve(template, values)
    required = [(declared, found) for declared, found in resolved if not declared.optional]
    type_matches = item.get("type") == template.type
    ## The item type counts as one more thing to match, so total is never 0.
    missing = tuple(column_of(declared) for declared, found in required if not found)
    filled = len(required) - len(missing) + int(type_matches)
    total = len(required) + 1
    terms, hundredths, entries = _format_terms(item, resolved, values, formats)
    ## counted in hundredths so a sum never shows float noise, and a whole number stays an int
    whole, part = divmod(hundredths, 100)
    all_filled = (filled * 100 + hundredths) / 100 if part else filled + whole
    total += terms
    return Score(
        template=template.name,
        score=all_filled / total,
        typeMatches=type_matches,
        filled=all_filled,
        total=total,
        missing=missing,
        formats=entries,
        optionalFilled=sum(bool(found) for declared, found in resolved if declared.optional),
    )


def fill_score(
    item: dict[str, Any], template: Template, datatypes: Iterable[Datatype] = ()
) -> Score:
    """The share of the template's coordinates plus its type that the item has (filled/total)."""
    return _score(item, template, flatten(item, Lookups()), scoring(datatypes))


def scores(
    item: dict[str, Any], templates: list[Template], datatypes: Iterable[Datatype] = ()
) -> list[Score]:
    return scores_with(item, templates, scoring(datatypes))


def scores_with(item: dict[str, Any], templates: list[Template], formats: Scoring) -> list[Score]:
    values = flatten(item, Lookups())
    return [_score(item, template, values, formats) for template in templates]


def best_match(templates: list[Template], item_scores: list[Score]) -> Template | None:
    """The full match with most required coordinates, then with most format terms, then with most
    optional coordinates filled, the first one listed on a tie."""
    best: tuple[int, int, int] | None = None
    found: Template | None = None
    for template, score in zip(templates, item_scores, strict=True):
        if not score.full:
            continue
        required = template.required_count()
        ## total is the required coordinates plus 1 plus the format terms
        key = (required, score.total - required, score.optionalFilled)
        if best is None or key > best:
            best, found = key, template
    return found


def matches(
    item: dict[str, Any], templates: list[Template], datatypes: Iterable[Datatype] = ()
) -> Template | None:
    return best_match(templates, scores(item, templates, datatypes))


def _format_json(entry: FormatScore) -> dict[str, Any]:
    return {
        "column": entry.column,
        "datatype": entry.datatype,
        "score": entry.score,
        "distance": entry.distance,
        "firstEdit": entry.firstEdit,
        "expected": entry.expected,
        "tooLong": entry.tooLong,
    }


def score_json(score: Score) -> dict[str, Any]:
    answer = {
        "template": score.template,
        "score": score.score,
        "typeMatches": score.typeMatches,
        "filled": score.filled,
        "total": score.total,
        "missing": list(score.missing),
    }
    if score.formats:
        answer["formats"] = [_format_json(entry) for entry in score.formats]
    return answer

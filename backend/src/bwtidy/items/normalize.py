"""Normalizing (docs/spec.md): rename fields, merge columns and items, near-duplicate hints.

The functions here change a raw item dict in place, the same way set_leaf does, so the write
path can run them on a copy of the cached item to validate and on the full GET to write.
Every ValueError message names columns and items, never a value.
"""

from collections import defaultdict
from collections.abc import Iterable
from typing import Any

from bwtidy.items.coordinates import (
    GROUP_OF_TYPE,
    HIDDEN_FIELD,
    ITEM_TYPES,
    MULTI_PATHS,
    NOT_LEAVES,
    Coordinate,
    Lookups,
    Value,
    field_entries,
    field_types,
    flatten,
    is_secret,
    parse,
    remove_leaf,
    set_leaf,
    sort_key,
)


def near_duplicate_groups(columns: Iterable[str]) -> list[dict[str, Any]]:
    """Field columns whose names are equal after case folding and trimming whitespace.

    Only groups of two or more names count. The second occurrence of a name in one item
    (`fields.WPA key#2`) is the same name, not a near duplicate, so it is left out.
    """
    groups: dict[str, set[Coordinate]] = defaultdict(set)
    for display in columns:
        try:
            coordinate = parse(display)
        except ValueError:
            continue
        if coordinate.kind != "field" or coordinate.occurrence != 1:
            continue
        groups[coordinate.name.strip().casefold()].add(coordinate)
    return [
        {"key": key, "columns": [c.display for c in sorted(members, key=sort_key)]}
        for key, members in sorted(groups.items())
        if len(members) > 1
    ]


def _source(display: str) -> Coordinate:
    coordinate = parse(display)
    if coordinate.kind == "path" and coordinate.path in NOT_LEAVES:
        raise ValueError(f"{coordinate} is not a leaf and cannot be moved")
    return coordinate


def _new_field_type(source: Coordinate, types: dict[Coordinate, int]) -> int:
    """The type a new field gets when it takes the value of source: secret stays secret."""
    if source.kind == "field":
        return types.get(source, 0)
    return HIDDEN_FIELD if is_secret(source) else 0


def _check_slot(item: dict[str, Any], target: Coordinate) -> None:
    group = target.group
    item_type = item.get("type", 0)
    if target.kind == "path" and group != "item" and GROUP_OF_TYPE.get(item_type) != group:
        type_name = ITEM_TYPES.get(item_type, "this item type")
        raise ValueError(
            f"item {item.get('id')}: a {type_name} has no {target}, "
            "use template conversion to change its type"
        )


def _put(
    item: dict[str, Any],
    target: Coordinate,
    value: Value,
    source: Coordinate,
    source_types: dict[Coordinate, int],
) -> None:
    """Set target to a value taken from source, keeping the secret rule.

    source_types are the field types of the item source belongs to. A new target field gets
    the source's type, an existing one keeps its own.
    """
    new_type = _new_field_type(source, source_types)
    target_type = field_types(item).get(target, new_type) if target.kind == "field" else None
    if is_secret(source, source_types.get(source)) and not is_secret(target, target_type):
        raise ValueError(f"the secret {source} may not be moved into the non-secret {target}")
    set_leaf(item, target, value, new_type)


def has_field(item: dict[str, Any], name: str) -> bool:
    return any((f.get("name") or "") == name for f in item.get("fields") or [])


def rename_conflict(item: dict[str, Any], old: str, new: str) -> dict[str, Any] | None:
    """Renaming onto a name the item already has is a merge, so it is a conflict."""
    if not has_field(item, new):
        return None
    columns = [Coordinate.field(old).display, Coordinate.field(new).display]
    return {"itemId": item.get("id"), "columns": columns}


def rename_field(item: dict[str, Any], old: str, new: str) -> None:
    """Rename every field named old in the item.

    Raises ValueError when it has none, or already has a field named new (a merge).
    """
    if has_field(item, new):
        raise ValueError(f"item {item.get('id')} already has a field named {new!r}")
    fields = [f for f in item.get("fields") or [] if (f.get("name") or "") == old]
    if not fields:
        raise ValueError(f"item {item.get('id')} has no field named {old!r}")
    for entry in fields:
        entry["name"] = new


def merge_columns_of(sources: list[str], target: str) -> tuple[list[Coordinate], Coordinate]:
    """The parsed target and the sources without it. Raises ValueError for a bad column."""
    target_coordinate = parse(target)
    if target_coordinate.kind == "path" and target_coordinate.path in NOT_LEAVES:
        raise ValueError(f"{target_coordinate} is not a leaf and cannot be a merge target")
    parsed: list[Coordinate] = []
    for display in sources:
        coordinate = _source(display)
        if coordinate != target_coordinate and coordinate not in parsed:
            parsed.append(coordinate)
    if not parsed:
        raise ValueError("a merge needs at least one source column besides the target")
    return parsed, target_coordinate


def merge_values(
    item: dict[str, Any], sources: list[Coordinate], target: Coordinate, lookups: Lookups
) -> list[Coordinate]:
    """The merged columns (target first) that hold a value in this item."""
    values = flatten(item, lookups)
    return [c for c in (target, *sources) if c in values]


def merge_conflict(
    item: dict[str, Any],
    sources: list[Coordinate],
    target: Coordinate,
    resolution: str | None,
    lookups: Lookups,
) -> dict[str, Any] | None:
    """The merge conflict of one item: values in more than one merged column, no choice made."""
    present = merge_values(item, sources, target, lookups)
    if len(present) < 2 or resolution is not None:
        return None
    return {"itemId": item.get("id"), "columns": [c.display for c in present]}


def merge_columns(
    item: dict[str, Any],
    sources: list[Coordinate],
    target: Coordinate,
    resolution: str | None,
    lookups: Lookups,
) -> bool:
    """Move the item's value into target and remove the values not kept.

    resolution names the merged column to keep when more than one holds a value, it is not
    needed (and ignored) otherwise. Returns
    whether the item changed. Raises ValueError when the item's type lacks a built-in target,
    a value would break the secret rule, or a needed resolution is missing or wrong.
    """
    _check_slot(item, target)
    present = merge_values(item, sources, target, lookups)
    if len(present) < 2:
        keep = present[0] if present else None
    elif resolution is None:
        raise ValueError(f"item {item.get('id')} has values in more than one merged column")
    else:
        keep = parse(resolution)
        if keep not in present:
            raise ValueError(
                f"the resolution of item {item.get('id')} names {keep}, "
                "which holds no value among the merged columns"
            )
    if keep is None:
        return False
    ## captured before anything changes, removing or renaming a field renumbers its namesakes
    entries = dict(field_entries(item))
    doomed = [c for c in present if c not in (target, keep)]
    if keep != target:
        if _renames_in_place(item, keep, target):
            entries[keep]["name"] = target.name
        else:
            _put(item, target, flatten(item, lookups)[keep], keep, field_types(item))
            doomed.append(keep)
    gone = [entries[c] for c in doomed if c.kind == "field"]
    if gone:
        item["fields"] = [f for f in item["fields"] if not any(f is g for g in gone)]
    for coordinate in doomed:
        if coordinate.kind == "path":
            remove_leaf(item, coordinate)
    return keep != target or len(present) > 1


def _renames_in_place(item: dict[str, Any], keep: Coordinate, target: Coordinate) -> bool:
    """A field moving into a field the item lacks is renamed, keeping its type and position."""
    return (
        keep.kind == "field"
        and target.kind == "field"
        and target.occurrence == 1
        and not has_field(item, target.name)
    )


## the list columns whose entries a merge can add to the target's instead of replacing them
APPENDABLE = MULTI_PATHS - NOT_LEAVES


def _appended(mapping: dict[str, str], append: Iterable[str]) -> set[Coordinate]:
    """The parsed append columns. Raises ValueError for one that is no list or not mapped."""
    targets = {parse(display) for display in mapping}
    appended = set()
    for display in append:
        coordinate = parse(display)
        if coordinate.kind != "path" or coordinate.path not in APPENDABLE:
            raise ValueError(f"{coordinate} is not a list column a merge can add to")
        if coordinate not in targets:
            raise ValueError(f"{coordinate} is in append but not in the mapping")
        appended.add(coordinate)
    return appended


def _uri_entries(item: dict[str, Any]) -> list[dict[str, Any]]:
    login = item.get("login")
    uris = login.get("uris") if isinstance(login, dict) else None
    return [dict(entry) for entry in uris or [] if isinstance(entry, dict)]


def _append_uris(
    source: dict[str, Any], target: dict[str, Any], kept: list[dict[str, Any]]
) -> None:
    """Make the target's uris its kept entries, then the source's entries it lacks.

    Each entry keeps its own match setting. Runs after the checked _put of the source's list,
    so the source column was login.uris and the target has a login.
    """
    seen = {entry.get("uri") for entry in kept}
    added = []
    for entry in _uri_entries(source):
        uri = entry.get("uri")
        if uri and uri not in seen:
            seen.add(uri)
            added.append(entry)
    target["login"]["uris"] = kept + added


def merge_items(
    source: dict[str, Any],
    target: dict[str, Any],
    mapping: dict[str, str],
    lookups: Lookups,
    append: Iterable[str] = (),
) -> None:
    """Copy the mapped leaves {target column: source column} of source into target.

    A target column in append is a list column that keeps its entries and gets the source's
    entries it lacks added, instead of being replaced. A source column without a value is
    skipped. Raises ValueError for a column that is not a leaf, a slot the target's type
    lacks, a secret that would land in a non-secret column and an append column that is no
    list or not mapped.
    """
    appended = _appended(mapping, append)
    values = flatten(source, lookups)
    types = field_types(source)
    for target_display, source_display in mapping.items():
        from_coordinate = _source(source_display)
        to_coordinate = parse(target_display)
        _check_slot(target, to_coordinate)
        if from_coordinate not in values:
            continue
        kept = _uri_entries(target) if to_coordinate in appended else []
        _put(target, to_coordinate, values[from_coordinate], from_coordinate, types)
        if to_coordinate in appended:
            _append_uris(source, target, kept)

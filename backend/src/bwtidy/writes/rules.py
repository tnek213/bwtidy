"""The secret rules of docs/spec.md (Secret values) that every write checks: validate runs them
in batch order before the writes, plan_create on a new item and _apply_one on the item each
write GETs."""

import copy
from typing import Any

from bwtidy.items.coordinates import (
    HIDDEN_FIELD,
    READ_ONLY_PATHS,
    Coordinate,
    Lookups,
    field_types,
    flatten,
    is_secret,
    parse,
    set_leaf,
)
from bwtidy.operations import SetLeaf


class SecretIntoPlain(ValueError):
    """A write would make a non-secret value of an item equal one of its secret values."""


## columns nobody writes a value into: the type word, the organization and its collections
## (picked among existing names) and the read-only info columns
NOT_COMPARED = frozenset({"type", "organization", "collections"}) | READ_ONLY_PATHS


def _texts(
    item: dict[str, Any], lookups: Lookups, folder: str | None = None
) -> dict[Coordinate, tuple[bool, frozenset[str]]]:
    """Whether each compared column is secret, and its non-empty text values (a list column
    has several). folder, when given, is the folder name the item gets (a new item has no
    folder id yet, a move has another one)."""
    values = flatten(item, lookups)
    if folder is not None:
        values.pop(Coordinate.at("folder"), None)
        if folder:
            values[Coordinate.at("folder")] = folder
    types = field_types(item)
    found = {}
    for coordinate, value in values.items():
        if coordinate.kind == "path" and coordinate.path in NOT_COMPARED:
            continue
        listed = value if isinstance(value, list) else [value]
        texts = frozenset(text for text in listed if isinstance(text, str) and text)
        if texts:
            found[coordinate] = (is_secret(coordinate, types.get(coordinate)), texts)
    return found


def plain_matches_secret(
    after: dict[str, Any],
    before: dict[str, Any] | None,
    lookups: Lookups,
    *,
    folder: str | None = None,
    original: dict[str, Any] | None = None,
) -> None:
    """The secret rule of equal values: a non-secret value never equals a secret value of the
    same item, whichever of the two is written (docs/spec.md, Secret values).

    before is the item as it is, after as the write leaves it, before None for a new item.
    folder is the folder name after gets (see _texts). A pair of equal values that before
    already held in the same two columns is an existing collision and passes, so only a write
    of one of the two is refused. original, when given, is the item as the vault held it
    before the batch and the queue's pending entries: its secrets still count, so a secret
    that an earlier operation removed or replaced never goes into a plain column the write
    fills. Raises SecretIntoPlain naming both columns, never the value.
    """
    now = _texts(after, lookups, folder)
    was = {} if before is None else _texts(before, lookups)
    held = [(column, texts, True) for column, (secret, texts) in now.items() if secret]
    if original is not None:
        current = {column: texts for column, texts, _ in held}
        for column, (secret, texts) in _texts(original, lookups).items():
            gone = texts - current.get(column, frozenset())
            if secret and gone:
                held.append((column, gone, False))
    for secret_column, secrets, still_held in held:
        had_secret = was.get(secret_column, (False, frozenset()))
        for plain_column, (plain_is_secret, plains) in now.items():
            if plain_is_secret:
                continue
            had_plain = was.get(plain_column, (True, frozenset()))
            for value in secrets & plains:
                plain_written = had_plain[0] or value not in had_plain[1]
                secret_kept = had_secret[0] and value in had_secret[1]
                secret_written = still_held and not secret_kept
                if not (plain_written or secret_written):
                    continue
                if plain_written and not secret_written:
                    raise SecretIntoPlain(
                        f"a secret value may not be moved into the non-secret column "
                        f"{plain_column} from the secret column {secret_column} of the same item"
                    )
                raise SecretIntoPlain(
                    f"the non-secret column {plain_column} may not equal the secret column "
                    f"{secret_column} of the same item"
                )


def check_hidden_fits(item: dict[str, Any], coordinate: Coordinate, field_type: int | None) -> None:
    """An existing field keeps its type, so a hidden value would land in the open."""
    existing = field_types(item).get(coordinate)
    if field_type == HIDDEN_FIELD and existing not in (None, HIDDEN_FIELD):
        raise ValueError(
            f"{coordinate} is not a hidden field on this item, a hidden value cannot go there"
        )


def check_set_leaf_secrets(
    op: SetLeaf,
    item: dict[str, Any],
    lookups: Lookups,
    original: dict[str, Any] | None = None,
) -> None:
    """The secret rules of a setLeaf on the item the write GETs, right before its PUT: the rule
    of equal values (original as in plain_matches_secret) and check_hidden_fits.

    validate runs both in batch order before the writes, this runs them again so a field that
    the vault holds as text by now never takes a hidden value in the open.
    """
    coordinate = parse(op.column)
    after = copy.deepcopy(item)
    set_leaf(after, coordinate, op.value, op.fieldType)
    plain_matches_secret(after, item, lookups, original=original)
    check_hidden_fits(item, coordinate, op.fieldType)

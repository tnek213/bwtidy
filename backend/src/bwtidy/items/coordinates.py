"""Coordinates: where a value lives in an item, which is also its grid column (docs/spec.md).

A coordinate is structured and never parsed by splitting on dots, because custom field names
may contain dots. The display string is `login.username` for a built-in leaf and
`fields.<name>` for a custom field, with `#N` appended for the Nth field of the same name.
"""

import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Literal

type Value = str | int | bool | list[str]

FIELDS_PREFIX = "fields."
HIDDEN_FIELD = 1

## what a secret value shows as in the grid and in a queue entry's diff
MASKED = {"masked": True}

ITEM_TYPES = {1: "login", 2: "note", 3: "card", 4: "identity", 5: "sshKey"}

ITEM_PATHS = (
    "name",
    "type",
    "folder",
    "organization",
    "collections",
    "notes",
    "favorite",
    "reprompt",
    "attachments",
    "passkeys",
    "revisionDate",
)
LOGIN_KEYS = ("username", "password", "totp", "uris")
CARD_KEYS = ("cardholderName", "brand", "number", "expMonth", "expYear", "code")
IDENTITY_KEYS = (
    "title",
    "firstName",
    "middleName",
    "lastName",
    "address1",
    "address2",
    "address3",
    "city",
    "state",
    "postalCode",
    "country",
    "company",
    "email",
    "phone",
    "ssn",
    "username",
    "passportNumber",
    "licenseNumber",
)
SSH_KEY_KEYS = ("privateKey", "publicKey", "keyFingerprint")

## the item's sub-object for each group, in column order
SUB_OBJECTS = {
    "login": LOGIN_KEYS,
    "card": CARD_KEYS,
    "identity": IDENTITY_KEYS,
    "sshKey": SSH_KEY_KEYS,
}
GROUPS = ("item", *SUB_OBJECTS, "fields")

PATHS = (
    *ITEM_PATHS,
    *(f"{group}.{key}" for group, keys in SUB_OBJECTS.items() for key in keys),
)
PATH_ORDER = {path: index for index, path in enumerate(PATHS)}

## the sub-object that holds the built-in leaves of each item type, notes have none
GROUP_OF_TYPE = {1: "login", 3: "card", 4: "identity", 5: "sshKey"}

SECRET_PATHS = frozenset(
    {"login.password", "login.totp", "card.number", "card.code", "sshKey.privateKey"}
)
URIS = "login.uris"
MULTI_PATHS = frozenset({URIS, "collections", "attachments"})
READ_ONLY_PATHS = frozenset({"attachments", "passkeys", "revisionDate"})
## columns that change through their own operation (type, folder, organization, collections)
## or never (the read-only info columns), so set_leaf and remove_leaf refuse them
NOT_LEAVES = frozenset({"type", "folder", "organization", "collections"}) | READ_ONLY_PATHS
TEXT_FIELD = 0
BOOLEAN_FIELD = 2
LINKED_FIELD = 3

OCCURRENCE = re.compile(r"#([1-9][0-9]*)$")


@dataclass(frozen=True)
class Coordinate:
    kind: Literal["path", "field"]
    path: str = ""
    name: str = ""
    occurrence: int = 1

    @classmethod
    def at(cls, path: str) -> "Coordinate":
        if path not in PATH_ORDER:
            raise ValueError(f"Unknown coordinate path: {path}")
        return cls("path", path=path)

    @classmethod
    def field(cls, name: str, occurrence: int = 1) -> "Coordinate":
        if occurrence < 1:
            raise ValueError("A field occurrence starts at 1")
        return cls("field", name=name, occurrence=occurrence)

    @property
    def display(self) -> str:
        if self.kind == "path":
            return self.path
        ## a name that itself ends in #N always carries its occurrence, so parse gets it back
        if self.occurrence > 1 or OCCURRENCE.search(self.name):
            return f"{FIELDS_PREFIX}{self.name}#{self.occurrence}"
        return f"{FIELDS_PREFIX}{self.name}"

    @property
    def group(self) -> str:
        if self.kind == "field":
            return "fields"
        head, dot, _ = self.path.partition(".")
        return head if dot else "item"

    def __str__(self) -> str:
        return self.display


def parse(display: str) -> Coordinate:
    """Turn a display string back into a coordinate. Raises ValueError for an unknown one."""
    if display.startswith(FIELDS_PREFIX):
        name = display.removeprefix(FIELDS_PREFIX)
        match = OCCURRENCE.search(name)
        if match is None:
            return Coordinate.field(name)
        return Coordinate.field(name[: match.start()], int(match.group(1)))
    return Coordinate.at(display)


def is_secret(coordinate: Coordinate, field_type: int | None = None) -> bool:
    if coordinate.kind == "field":
        return field_type == HIDDEN_FIELD
    return coordinate.path in SECRET_PATHS


def sort_key(coordinate: Coordinate) -> tuple[int, int, str, str, int]:
    """Column order: built-in paths in spec order, then fields case-insensitively."""
    if coordinate.kind == "path":
        return (0, PATH_ORDER[coordinate.path], "", "", 0)
    name = coordinate.name
    return (1, 0, name.casefold(), name, coordinate.occurrence)


@dataclass(frozen=True)
class Lookups:
    """Names for the ids an item refers to."""

    folders: dict[str, str] = field(default_factory=dict)
    organizations: dict[str, str] = field(default_factory=dict)
    collections: dict[str, str] = field(default_factory=dict)

    @classmethod
    def build(
        cls,
        folders: list[dict[str, Any]],
        organizations: list[dict[str, Any]],
        collections: list[dict[str, Any]],
    ) -> "Lookups":
        def names(objects: list[dict[str, Any]]) -> dict[str, str]:
            ## bw lists a pseudo folder "No Folder" with a null id
            return {o["id"]: o.get("name") or "" for o in objects if o.get("id")}

        return cls(names(folders), names(organizations), names(collections))


def _empty(value: Any) -> bool:
    return value is None or value == "" or value == [] or value is False


def field_entries(item: dict[str, Any]) -> list[tuple[Coordinate, dict[str, Any]]]:
    """Every custom field with its coordinate, counting duplicates even when empty."""
    seen: Counter[str] = Counter()
    entries = []
    for entry in item.get("fields") or []:
        name = entry.get("name") or ""
        seen[name] += 1
        entries.append((Coordinate.field(name, seen[name]), entry))
    return entries


def field_types(item: dict[str, Any]) -> dict[Coordinate, int]:
    """The custom field type (0 text, 1 hidden, 2 boolean, 3 linked) of each field coordinate."""
    return {coordinate: entry.get("type", 0) for coordinate, entry in field_entries(item)}


def attachment_names(item: dict[str, Any]) -> list[str]:
    """The file names of the item's attachments in the item's order, never their url."""
    entries = [a for a in item.get("attachments") or [] if isinstance(a, dict)]
    return [name for a in entries if (name := a.get("fileName"))]


def flatten(item: dict[str, Any], lookups: Lookups) -> dict[Coordinate, Value]:
    """Every non-empty leaf of an item, keyed by coordinate.

    Leaves out what the spec says is never a leaf (ids, dates other than revisionDate,
    password history, passkey content, attachment metadata but the file names). favorite and
    reprompt are true when set. Linked fields carry no value of their own and are left out.
    """
    values: dict[Coordinate, Any] = {}

    def put(path: str, value: Any) -> None:
        if not _empty(value):
            values[Coordinate.at(path)] = value

    put("name", item.get("name"))
    put("type", ITEM_TYPES.get(item.get("type", 0), str(item.get("type"))))
    folder_id = item.get("folderId")
    if folder_id:
        put("folder", lookups.folders.get(folder_id, folder_id))
    org_id = item.get("organizationId")
    if org_id:
        put("organization", lookups.organizations.get(org_id, org_id))
    collection_ids = item.get("collectionIds") or []
    put(
        "collections",
        sorted((lookups.collections.get(c, c) for c in collection_ids), key=str.casefold),
    )
    put("notes", item.get("notes"))
    put("favorite", bool(item.get("favorite")))
    put("reprompt", bool(item.get("reprompt")))
    put("attachments", attachment_names(item))
    put("revisionDate", item.get("revisionDate"))

    for group, keys in SUB_OBJECTS.items():
        sub = item.get(group)
        if not isinstance(sub, dict):
            continue
        for key in keys:
            if (group, key) == ("login", "uris"):
                uris = [u.get("uri") for u in sub.get("uris") or [] if isinstance(u, dict)]
                put("login.uris", [u for u in uris if u])
            else:
                put(f"{group}.{key}", sub.get(key))
        if group == "login":
            put("passkeys", len(sub.get("fido2Credentials") or []) or None)

    for coordinate, entry in field_entries(item):
        value = entry.get("value")
        if not _empty(value):
            values[coordinate] = value
    return values


def _check_leaf(item: dict[str, Any], coordinate: Coordinate) -> None:
    """Refuse a coordinate that is not a writable leaf of this item. Messages carry no value."""
    if coordinate.kind == "field":
        return
    path = coordinate.path
    if path in NOT_LEAVES:
        raise ValueError(f"{path} is not a leaf, it changes through its own operation")
    group = coordinate.group
    if group != "item" and GROUP_OF_TYPE.get(item.get("type", 0)) != group:
        type_name = ITEM_TYPES.get(item.get("type", 0), "this item type")
        raise ValueError(f"a {type_name} has no {path}")


def _as_bool(value: Any, path: str) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str) and value.lower() in ("true", "false"):
        return value.lower() == "true"
    raise ValueError(f"{path} takes true or false")


def _as_text(value: Any, coordinate: Coordinate) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{coordinate} takes one text value")
    return value


def _as_uris(value: Any, existing: list[Any]) -> list[dict[str, Any]]:
    """Rebuild login.uris from a list of strings, keeping the match setting of a kept uri."""
    if not isinstance(value, list) or not all(isinstance(u, str) for u in value):
        raise ValueError("login.uris takes a list of text values")
    matches = {
        entry.get("uri"): entry.get("match") for entry in existing if isinstance(entry, dict)
    }
    return [{"match": matches.get(uri), "uri": uri} for uri in value if uri]


## Bitwarden's URI match settings by number. None is the client's default, a setting of the
## browser extension that is not in the vault
type MatchNumber = Literal[0, 1, 2, 3, 4, 5]
type UriMatch = MatchNumber | None
URI_MATCHES: dict[MatchNumber, str] = {
    0: "domain",
    1: "host",
    2: "startsWith",
    3: "exact",
    4: "regex",
    5: "never",
}
REGEX_MATCH = 4
LOGIN_TYPE = 1


def uri_entries(item: dict[str, Any]) -> list[tuple[str, UriMatch]]:
    """The URIs of a login in order, each with its match setting. An entry without a uri is
    left out, as in flatten."""
    sub = item.get("login")
    if not isinstance(sub, dict):
        return []
    return [
        (entry["uri"], entry.get("match"))
        for entry in sub.get("uris") or []
        if isinstance(entry, dict) and isinstance(entry.get("uri"), str) and entry["uri"]
    ]


def uris_shown(uris: list[tuple[str, UriMatch]]) -> list[dict[str, Any]]:
    """URIs as MCP and the queue's diff show them: {"uri", "match"}, the match as its word, None
    for the client's default."""
    return [{"uri": uri, "match": URI_MATCHES.get(match, match)} for uri, match in uris]


def check_uris(uris: list[tuple[str, UriMatch]]) -> None:
    """Refuse an empty uri, an unknown match and a regex that Python cannot compile.

    Bitwarden evaluates a regex as a JavaScript one, so this catches only what Python rejects
    too. The message names the URI by its position, never its text.
    """
    for number, (uri, match) in enumerate(uris, 1):
        if not uri.strip():
            raise ValueError(f"URI {number} is empty")
        if match is not None and match not in URI_MATCHES:
            raise ValueError(f"URI {number} has an unknown match, use 0 to 5 or null")
        if match == REGEX_MATCH:
            try:
                re.compile(uri)
            except re.error:
                raise ValueError(f"URI {number} is not a valid regular expression") from None


def set_uris(item: dict[str, Any], uris: list[tuple[str, UriMatch]]) -> None:
    """Replace the whole URI list of a login with uris, in their order and with their match
    settings. Raises ValueError for another type and for what check_uris refuses."""
    if item.get("type") != LOGIN_TYPE:
        raise ValueError("only a login has URIs")
    check_uris(uris)
    sub = item.get("login")
    if not isinstance(sub, dict):
        sub = item["login"] = {}
    sub["uris"] = [{"match": match, "uri": uri} for uri, match in uris]


def _field_value(value: Any, field_type: int, coordinate: Coordinate) -> str:
    if field_type == BOOLEAN_FIELD:
        return "true" if _as_bool(value, coordinate.display) else "false"
    return _as_text(value, coordinate)


def set_leaf(
    item: dict[str, Any], coordinate: Coordinate, value: Value, field_type: int | None = None
) -> None:
    """Set one leaf in the raw item dict from bw serve, leaving every other key untouched.

    A custom field that does not exist yet is appended with field_type (0 text when None).
    An existing field keeps its type. Raises ValueError, without the value, when the
    coordinate is not a writable leaf of this item or the value has the wrong shape.
    """
    _check_leaf(item, coordinate)
    if coordinate.kind == "field":
        _set_field(item, coordinate, value, field_type)
        return
    path = coordinate.path
    if path == "name":
        if not _as_text(value, coordinate):
            raise ValueError("name may not be empty")
        item["name"] = value
    elif path == "notes":
        item["notes"] = _as_text(value, coordinate)
    elif path == "favorite":
        item["favorite"] = _as_bool(value, path)
    elif path == "reprompt":
        item["reprompt"] = 1 if _as_bool(value, path) else 0
    else:
        group, _, key = path.partition(".")
        sub = item.get(group)
        if not isinstance(sub, dict):
            sub = item[group] = {}
        if path == "login.uris":
            sub["uris"] = _as_uris(value, sub.get("uris") or [])
        else:
            sub[key] = _as_text(value, coordinate)


def _set_field(
    item: dict[str, Any], coordinate: Coordinate, value: Value, field_type: int | None
) -> None:
    entries = field_entries(item)
    for existing, entry in entries:
        if existing == coordinate:
            if entry.get("type") == LINKED_FIELD:
                raise ValueError(f"{coordinate} is a linked field and has no value of its own")
            entry["value"] = _field_value(value, entry.get("type", 0), coordinate)
            return
    count = sum(1 for existing, _ in entries if existing.name == coordinate.name)
    if coordinate.occurrence != count + 1:
        raise ValueError(_fill_first(coordinate.name, count))
    new_type = 0 if field_type is None else field_type
    if new_type not in (0, HIDDEN_FIELD, BOOLEAN_FIELD):
        raise ValueError("a new field is text (0), hidden (1) or boolean (2)")
    if not isinstance(item.get("fields"), list):
        item["fields"] = []
    item["fields"].append(
        {
            "name": coordinate.name,
            "value": _field_value(value, new_type, coordinate),
            "type": new_type,
            "linkedId": None,
        }
    )


def _fill_first(name: str, count: int) -> str:
    """The occurrence to fill before a later one can be added. Names the field, never a value."""
    first = Coordinate.field(name, count + 1)
    if count == 0:
        return f"fill {first} first, the item has no {name} field yet"
    fields = "field" if count == 1 else "fields"
    return f"fill {first} first, the item has only {count} {name} {fields}"


def remove_leaf(item: dict[str, Any], coordinate: Coordinate) -> None:
    """Remove a custom field or clear a built-in leaf, leaving every other key untouched.

    Raises ValueError for name (required), for a field that does not exist and for
    anything that is not a writable leaf of this item.
    """
    _check_leaf(item, coordinate)
    if coordinate.kind == "field":
        for existing, entry in field_entries(item):
            if existing == coordinate:
                item["fields"] = [f for f in item["fields"] if f is not entry]
                return
        raise ValueError(f"{coordinate} does not exist")
    path = coordinate.path
    if path == "name":
        raise ValueError("name is required and cannot be removed")
    if path == "notes":
        item["notes"] = None
    elif path == "favorite":
        item["favorite"] = False
    elif path == "reprompt":
        item["reprompt"] = 0
    else:
        group, _, key = path.partition(".")
        sub = item.get(group)
        if isinstance(sub, dict):
            sub[key] = [] if path == "login.uris" else None

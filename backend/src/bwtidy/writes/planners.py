"""A planner computes what an operation makes of an item, without writing. validate runs it on
a copy of the cached item, the queue's diff shows what it makes and _apply_one writes what it
makes of the item it GETs. The folder kinds have their checks and the plan of a restoreFolder
here too, on a list of folders: the cached one, or bw serve's at write time, and so has the
rename of an attachment, on the item."""

import copy
from dataclasses import dataclass
from typing import Any, Literal

from bwtidy.backups import BackupContents
from bwtidy.items.coordinates import (
    GROUP_OF_TYPE,
    HIDDEN_FIELD,
    ITEM_TYPES,
    NOT_LEAVES,
    READ_ONLY_PATHS,
    SUB_OBJECTS,
    Coordinate,
    Lookups,
    field_entries,
    field_types,
    flatten,
    is_secret,
    parse,
    remove_leaf,
    set_leaf,
)
from bwtidy.operations import (
    ATTACHMENTS,
    CreateItem,
    Operation,
    RenameAttachment,
    RestoreFolder,
    attachment_of,
    shown_attachment,
    shown_folder,
    touched,
)
from bwtidy.sections.templates import Template
from bwtidy.vault import TOOL_NOTE_NAME, ToolNoteId, VaultCache, template_named
from bwtidy.writes.restore_plan import plan_copy
from bwtidy.writes.rules import plain_matches_secret

LOGIN = 1
SECURE_NOTE = 2


def by_name(objects: list[dict[str, Any]], name: str) -> dict[str, Any] | None:
    """The first of objects (folders, organizations) with an id and this name, else None."""
    return next((o for o in objects if o.get("id") and o.get("name") == name), None)


def resolve_collections(cache: VaultCache, organization_id: str, names: list[str]) -> list[str]:
    """Collection ids for names inside one organization. Raises ValueError for an unknown one."""
    if not names:
        raise ValueError("an organization item needs at least one collection")
    ids = []
    for name in names:
        collection = next(
            (
                c
                for c in cache.collections
                if c.get("organizationId") == organization_id and c.get("name") == name
            ),
            None,
        )
        if collection is None:
            raise ValueError(f"the organization has no collection named {name!r}")
        ids.append(collection["id"])
    return ids


GENERATE_COLUMNS = "a generated value goes only into login.password or a hidden field"


def check_generate_leaf(item: dict[str, Any], column: str) -> Coordinate:
    """The coordinate a generateLeaf may write: login.password of a login, or a hidden custom
    field (a missing one is created hidden). Raises ValueError for any other column."""
    coordinate = parse(column)
    if coordinate.kind == "field":
        field_type = field_types(item).get(coordinate, HIDDEN_FIELD)
        if field_type != HIDDEN_FIELD:
            raise ValueError(GENERATE_COLUMNS)
    elif coordinate != Coordinate.at("login.password"):
        raise ValueError(GENERATE_COLUMNS)
    ## the same checks a setLeaf passes (a login only, a field occurrence in order), no value
    set_leaf(copy.deepcopy(item), coordinate, "", HIDDEN_FIELD)
    return coordinate


def _generate_columns(op: CreateItem) -> list[Coordinate]:
    """A createItem's generateColumns, each once. Raises ValueError for what is no column."""
    try:
        return list(dict.fromkeys(parse(column) for column in op.generateColumns))
    except ValueError:
        raise ValueError(GENERATE_COLUMNS) from None


def generated_coordinates(op: CreateItem) -> list[Coordinate]:
    """Every column of a createItem that bw generates at write time, each once, also a
    login.password asked for both as generatePassword and in generateColumns."""
    asked = [Coordinate.at("login.password")] if op.generatePassword else []
    return list(dict.fromkeys([*asked, *_generate_columns(op)]))


def _check_generate_columns(planned: dict[str, Any], columns: list[Coordinate]) -> None:
    """Refuse a column check_generate_leaf refuses on the planned item. Each one goes on a copy
    as the write will set it, so the second occurrence of a new field sees the first."""
    item = copy.deepcopy(planned)
    try:
        for coordinate in columns:
            check_generate_leaf(item, coordinate.display)
            set_leaf(item, coordinate, "", HIDDEN_FIELD)
    except ValueError:
        raise ValueError(GENERATE_COLUMNS) from None


## sources that are no leaf of their own and so cannot be mapped anywhere
NOT_SOURCES = NOT_LEAVES


def empty_sub_object(item_type: int) -> dict[str, Any]:
    if item_type == SECURE_NOTE:
        return {"secureNote": {"type": 0}}
    group = GROUP_OF_TYPE[item_type]
    sub: dict[str, Any] = {key: None for key in SUB_OBJECTS[group]}
    if group == "login":
        sub["uris"] = []
    return {group: sub}


def plan_conversion(
    item: dict[str, Any],
    new_type: int,
    mapping: dict[str, str],
    lookups: Lookups,
    new_field_types: dict[Coordinate, int] | None = None,
) -> dict[str, Any]:
    """The object to POST for a type change of item, with mapping {target: source}.

    Folder, organization, collections, favorite, reprompt, notes and the custom fields come
    along. A mapped source moves (a custom field source is dropped, notes cleared). An
    unmapped built-in leaf of the old type becomes a custom field named after its key,
    hidden when the leaf is secret. A mapped target field that does not exist yet gets its
    type from new_field_types, else hidden for a secret source and text for any other.
    Raises ValueError for anything not allowed.
    """
    old_type = item.get("type", 0)
    if new_type not in ITEM_TYPES:
        raise ValueError(f"unknown item type {new_type}")
    if new_type == old_type:
        raise ValueError("the item already has that type")
    values = flatten(item, lookups)
    types = field_types(item)
    in_org = bool(item.get("organizationId"))
    new: dict[str, Any] = {
        "type": new_type,
        "name": item.get("name"),
        "notes": item.get("notes"),
        "favorite": bool(item.get("favorite")),
        "reprompt": item.get("reprompt") or 0,
        "folderId": item.get("folderId"),
        "organizationId": item.get("organizationId"),
        "collectionIds": list(item.get("collectionIds") or []) if in_org else None,
        "fields": copy.deepcopy(item.get("fields") or []),
        **empty_sub_object(new_type),
    }
    parsed = {parse(target): parse(source) for target, source in mapping.items()}
    for target, source in parsed.items():
        if source.kind == "path" and source.path in NOT_SOURCES:
            raise ValueError(f"{source} cannot be mapped, it is no leaf")
        if source == target or source not in values:
            continue
        source_secret = is_secret(source, types.get(source))
        new_field_type = (new_field_types or {}).get(target, HIDDEN_FIELD if source_secret else 0)
        target_type = types.get(target, new_field_type) if target.kind == "field" else None
        if source_secret and not is_secret(target, target_type):
            raise ValueError(f"the secret {source} may not be mapped into the non-secret {target}")
        set_leaf(new, target, values[source], new_field_type)
    moved = {source for target, source in parsed.items() if source in values and source != target}
    moved -= set(parsed)
    dropped = [entry for coordinate, entry in field_entries(new) if coordinate in moved]
    new["fields"] = [entry for entry in new["fields"] if not any(entry is d for d in dropped)]
    if Coordinate.at("notes") in moved:
        new["notes"] = None
    old_group = GROUP_OF_TYPE.get(old_type)
    if old_group is not None:
        for key in SUB_OBJECTS[old_group]:
            coordinate = Coordinate.at(f"{old_group}.{key}")
            value = values.get(coordinate)
            if value is None or coordinate in moved:
                continue
            text = "\n".join(value) if isinstance(value, list) else str(value)
            new["fields"].append(
                {
                    "name": key,
                    "value": text,
                    "type": HIDDEN_FIELD if is_secret(coordinate) else 0,
                    "linkedId": None,
                }
            )
    return new


def plan_personal(item: dict[str, Any]) -> dict[str, Any]:
    """The object to POST for moving an organization item into the personal vault.

    Everything stays as it is (the folder too), except the organization and collections.
    """
    new = plan_copy(item)
    new["organizationId"] = None
    new["collectionIds"] = []
    return new


def _template_mapping(template: Template, mapping: dict[str, str]) -> dict[Coordinate, Coordinate]:
    coordinates = set(template.coordinate_list())
    parsed: dict[Coordinate, Coordinate] = {}
    for target_column, source_column in mapping.items():
        target, source = parse(target_column), parse(source_column)
        if target not in coordinates:
            raise ValueError(f"{target} is no coordinate of the template {template.name!r}")
        if source.kind == "path" and source.path in NOT_SOURCES:
            raise ValueError(f"{source} cannot be mapped, it is no leaf")
        parsed[target] = source
    return parsed


def add_missing_fields(item: dict[str, Any], template: Template) -> None:
    """Append every custom field of the template the item lacks, empty, with its field type."""
    present = {coordinate for coordinate, _ in field_entries(item)}
    for coordinate, field_type in template.field_types().items():
        if coordinate in present:
            continue
        if not isinstance(item.get("fields"), list):
            item["fields"] = []
        item["fields"].append(
            {"name": coordinate.name, "value": None, "type": field_type, "linkedId": None}
        )


def _move_within(
    item: dict[str, Any],
    template: Template,
    parsed: dict[Coordinate, Coordinate],
    lookups: Lookups,
) -> dict[str, Any]:
    """A copy of item with the mapped values moved, for a template of the item's own type.

    A mapped custom field source is removed, a built-in source cleared (name stays, it is
    required). Everything unmapped stays as it is.
    """
    new = copy.deepcopy(item)
    values = flatten(item, lookups)
    types = field_types(item)
    wanted = template.field_types()
    moved = {source for target, source in parsed.items() if source in values and source != target}
    moved -= set(parsed)
    ## entries are dropped by identity after the targets are set, so duplicate names keep
    ## their occurrence numbers while the targets are written
    dropped = [entry for coordinate, entry in field_entries(new) if coordinate in moved]
    for target, source in parsed.items():
        if source == target or source not in values:
            continue
        field_type = wanted.get(target, 0)
        target_type = types.get(target, field_type) if target.kind == "field" else None
        if is_secret(source, types.get(source)) and not is_secret(target, target_type):
            raise ValueError(f"the secret {source} may not be mapped into the non-secret {target}")
        set_leaf(new, target, values[source], field_type)
    new["fields"] = [e for e in new.get("fields") or [] if not any(e is d for d in dropped)]
    for source in moved:
        if source.kind == "path" and source.path != "name":
            remove_leaf(new, source)
    return new


def plan_template(
    item: dict[str, Any], template: Template, mapping: dict[str, str], lookups: Lookups
) -> dict[str, Any]:
    """The item after applying template with mapping {template column: source column}.

    Same type: the full item to PUT, with the mapped values moved. Another type: the object
    to POST for the type change (see plan_conversion). Either way every template field the
    item lacks is added empty with the template's field type. Raises ValueError for anything
    not allowed, the secret rule included (a text source into a hidden field is fine).
    """
    parsed = _template_mapping(template, mapping)
    if item.get("type") == template.type:
        new = _move_within(item, template, parsed, lookups)
    else:
        new = plan_conversion(item, template.type, mapping, lookups, template.field_types())
    add_missing_fields(new, template)
    return new


def changed_columns(before: dict[str, Any], after: dict[str, Any], lookups: Lookups) -> list[str]:
    """The coordinates whose value differs between two states of an item, or a custom field
    only one of them has (a template adds its fields empty), after's first.

    The read-only columns are left out: a conversion copies the attachments after the create.
    Values are compared here only and never returned.
    """
    old, new = flatten(before, lookups), flatten(after, lookups)
    old_fields, new_fields = field_types(before), field_types(after)
    return [
        coordinate.display
        for coordinate in dict.fromkeys([*new, *new_fields, *old, *old_fields])
        if not (coordinate.kind == "path" and coordinate.path in READ_ONLY_PATHS)
        and (
            old.get(coordinate) != new.get(coordinate)
            or (coordinate in old_fields) != (coordinate in new_fields)
        )
    ]


def created_type(op: CreateItem, cache: VaultCache, remembered: ToolNoteId) -> int:
    """The item type of a createItem: its type, or its template's. Raises ValueError."""
    if (op.type is None) == (op.template is None):
        raise ValueError("a new item needs exactly one of type and template")
    if op.template is not None:
        return template_named(cache, remembered, op.template).type
    if op.type not in ITEM_TYPES:
        raise ValueError(f"unknown item type {op.type}")
    return op.type


def plan_create(op: CreateItem, cache: VaultCache, remembered: ToolNoteId) -> dict[str, Any]:
    """The object to POST for a createItem, without its folder and its generated values.

    The type's empty sub object, the name, the organization and its collections, a template's
    custom fields empty with their field types, then the values set by column like setLeaf
    sets them (a custom field the template does not name becomes a text field), then the
    secretValues (one the template does not name becomes a hidden field). Raises ValueError for
    anything refused, a value in values for a secret column among them, and a column to
    generate that takes no generated value or also has a value, and a secret value equal to
    a plain one (plain_matches_secret). A queued entry's secretValues hold ciphertexts here,
    which plan the same.
    """
    item_type = created_type(op, cache, remembered)
    template = template_named(cache, remembered, op.template) if op.template is not None else None
    if not op.name.strip():
        raise ValueError("a new item needs a name")
    if op.generatePassword and item_type != LOGIN:
        raise ValueError("only a login can get a generated password")
    organization_id = None
    collection_ids = None
    if op.organizationName:
        organization = by_name(cache.organizations, op.organizationName)
        if organization is None:
            raise ValueError(f"no organization named {op.organizationName!r}")
        organization_id = organization["id"]
        collection_ids = resolve_collections(cache, organization_id, op.collectionNames)
    elif op.collectionNames:
        raise ValueError("collections need an organization")
    elif item_type == SECURE_NOTE and op.name == TOOL_NOTE_NAME:
        raise ValueError(
            f"a personal secure note named {TOOL_NOTE_NAME} would be taken for bwtidy's own note"
        )
    new: dict[str, Any] = {
        "type": item_type,
        "name": op.name,
        "notes": None,
        "favorite": False,
        "reprompt": 0,
        "folderId": None,
        "organizationId": organization_id,
        "collectionIds": collection_ids,
        "fields": [],
        **empty_sub_object(item_type),
    }
    if template is not None:
        add_missing_fields(new, template)
    generate_columns = _generate_columns(op)
    if any(parse(column) in generate_columns for column in op.values):
        raise ValueError(GENERATE_COLUMNS)
    for column, value in op.values.items():
        coordinate = parse(column)
        if coordinate == Coordinate.at("name"):
            raise ValueError("the name of a new item is given as name, not as a value")
        set_leaf(new, coordinate, value)
        if is_secret(coordinate, field_types(new).get(coordinate)):
            raise ValueError(
                f"{coordinate} is secret: a new item never takes a secret value, fill it in "
                "the grid after it is created"
            )
    _check_generate_columns(new, generate_columns)
    _set_secret_values(new, op)
    plain_matches_secret(new, None, cache.lookups(), folder=op.folderName)
    return new


def _set_secret_values(new: dict[str, Any], op: CreateItem) -> None:
    """Set a createItem's secretValues, secret columns only. Messages name no value."""
    for column, value in op.secretValues.items():
        coordinate = parse(column)
        if not value:
            raise ValueError(f"the secret value of {coordinate} is empty, leave the column out")
        if coordinate in generated_coordinates(op):
            raise ValueError(f"{coordinate} is generated, it cannot take a value as well")
        if coordinate == Coordinate.at("name"):
            raise ValueError("the name of a new item is given as name, not as a secret value")
        set_leaf(new, coordinate, value, HIDDEN_FIELD)
        if not is_secret(coordinate, field_types(new).get(coordinate)):
            raise ValueError(f"{coordinate} is not secret, its value goes in values")


def folder_with_id(folders: list[dict[str, Any]], folder_id: str) -> dict[str, Any] | None:
    return next((f for f in folders if f.get("id") and f["id"] == folder_id), None)


def other_folder_named(
    folders: list[dict[str, Any]], name: str, folder_id: str | None
) -> dict[str, Any] | None:
    """A folder other than folder_id whose name is name, ignoring case."""
    wanted = name.casefold()
    return next(
        (
            f
            for f in folders
            if f.get("id") and f["id"] != folder_id and (f.get("name") or "").casefold() == wanted
        ),
        None,
    )


def check_folder_name(folders: list[dict[str, Any]], folder_id: str, name: str) -> None:
    """A folder's new name: not empty, not its name now, no other folder's ignoring case."""
    if not name.strip():
        raise ValueError("the new folder name is empty")
    folder = folder_with_id(folders, folder_id)
    if folder is not None and folder.get("name") == name:
        raise ValueError("the folder has that name already")
    if other_folder_named(folders, name, folder_id) is not None:
        raise ValueError(f'another folder is named "{name}"')


def folder_conflict(folders: list[dict[str, Any]], op: Operation) -> dict[str, Any] | None:
    """The conflict of a rename or delete whose folder is gone or no longer has the name the
    client showed (shown_folder), None when it holds."""
    for folder_id, shown_name in shown_folder(op).items():
        folder = folder_with_id(folders, folder_id)
        actual = folder.get("name") if folder is not None else None
        if actual != shown_name:
            return {"folderId": folder_id, "expected": shown_name, "actual": actual}
    return None


def check_attachment_rename(item: dict[str, Any], op: RenameAttachment) -> dict[str, Any]:
    """The attachment a rename is about, refused (ValueError) when the item has none with its
    id, the new name is empty, holds a slash or a backslash, is the attachment's name now, or is
    the name of another attachment of the item (ignoring case, str.casefold).

    A slash would be a directory in the backup's item-attachments, and a name with one cannot
    be saved under it on any system. Another attachment's name, in any case, would let a
    download of both to a file system that ignores case overwrite one with the other. Changing
    only the case of the attachment's own name is allowed. Returns the attachment.
    """
    attachment = attachment_of(item, op.attachmentId)
    if attachment is None:
        raise ValueError(f"the item has no attachment with the id {op.attachmentId}")
    if not op.fileName.strip():
        raise ValueError("the new file name is empty")
    if "/" in op.fileName or "\\" in op.fileName:
        raise ValueError("a file name may not hold a slash or a backslash")
    if op.fileName == attachment.get("fileName"):
        raise ValueError("the attachment has that name already")
    wanted = op.fileName.casefold()
    for other in item.get("attachments") or []:
        if not isinstance(other, dict) or other.get("id") == op.attachmentId:
            continue
        name = other.get("fileName")
        if isinstance(name, str) and name.casefold() == wanted:
            raise ValueError(f'the item has another attachment named "{name}"')
    return attachment


def attachment_conflict(item: dict[str, Any], op: Operation) -> dict[str, Any] | None:
    """The conflict of an attachment rename whose attachment has another name than the one the
    proposer saw (shown_attachment), None when it holds or the attachment is gone (the rename's
    own check refuses that).

    It is shaped like a conflict of a shown value: both revisionDates (which can be equal, as
    an attachment's changes leave the revisionDate alone) and the column attachments.
    """
    for item_id, (attachment_id, shown_name) in shown_attachment(op).items():
        attachment = attachment_of(item, attachment_id) if item.get("id") == item_id else None
        if attachment is not None and attachment.get("fileName") != shown_name:
            expected = touched(op).get(item_id, "")
            actual = item.get("revisionDate")
            return {
                "itemId": item_id,
                "expected": expected,
                "actual": actual,
                "column": ATTACHMENTS,
            }
    return None


def in_folder(items: list[dict[str, Any]], folder_id: str) -> list[dict[str, Any]]:
    return [item for item in items if item.get("folderId") == folder_id]


def _items(count: int) -> str:
    return f"{count} item" if count == 1 else f"{count} items"


def check_folder_empty(
    items: list[dict[str, Any]], trashed: list[dict[str, Any]], folder_id: str
) -> None:
    """Refuse the delete of a folder that holds an item. One in the trash counts: the delete
    would take it out of the folder too."""
    active, trash = len(in_folder(items, folder_id)), len(in_folder(trashed, folder_id))
    if active:
        raise ValueError(f"the folder holds {_items(active)}, move them out first")
    if trash:
        raise ValueError(
            f"the folder holds {_items(trash)} in the trash, delete them for good or take "
            "them out of the trash and move them out first"
        )


def check_folder_name_secrets(
    items: list[dict[str, Any]],
    folder_id: str,
    name: str,
    lookups: Lookups,
    originals: dict[str, dict[str, Any]],
) -> None:
    """The rule of equal values for a folder's new name: it becomes the folder column of every
    item in the folder, so it may not equal a secret value of one of them (like moveToFolder).
    originals holds the items as the vault held them, whose secrets still count."""
    for item in in_folder(items, folder_id):
        original = originals.get(item.get("id") or "")
        plain_matches_secret(item, item, lookups, folder=name, original=original)


## what a restoreFolder does with its folder
FolderAction = Literal["renameBack", "createAgain", "leave"]
RENAME_BACK: FolderAction = "renameBack"
CREATE_AGAIN: FolderAction = "createAgain"
LEAVE_FOLDER: FolderAction = "leave"


@dataclass(frozen=True)
class FolderRestore:
    """The plan of a restoreFolder: the name the backup holds and the folder's name now (None
    when it is gone)."""

    folder_id: str
    action: FolderAction
    name: str
    now: str | None

    @property
    def text(self) -> str:
        if self.action == RENAME_BACK:
            return f'rename back to "{self.name}"'
        if self.action == CREATE_AGAIN:
            return f'create again as "{self.name}"'
        if self.now is None:
            return f'leave as it is, a folder named "{self.name}" exists'
        return "leave as it is"


def plan_folder_restore(
    op: RestoreFolder, contents: BackupContents, folders: list[dict[str, Any]]
) -> FolderRestore:
    """Rename the folder back to its name in the backup, or create it again with that name
    when it is gone. Nothing when it has the name, or when it is gone and a folder of that
    name exists (one a move created meanwhile, say). Raises ValueError when the backup holds
    no such folder, or when another folder has the name the folder would get back."""
    name = contents.folder_names.get(op.folderId)
    if not name:
        raise ValueError(f"the backup {contents.name} holds no folder with the id {op.folderId}")
    folder = folder_with_id(folders, op.folderId)
    if folder is None:
        exists = other_folder_named(folders, name, None) is not None
        action = LEAVE_FOLDER if exists else CREATE_AGAIN
        return FolderRestore(op.folderId, action, name, None)
    now = folder.get("name") or ""
    if now == name:
        return FolderRestore(op.folderId, LEAVE_FOLDER, name, now)
    if other_folder_named(folders, name, op.folderId) is not None:
        raise ValueError(f'another folder is named "{name}", rename it first')
    return FolderRestore(op.folderId, RENAME_BACK, name, now)

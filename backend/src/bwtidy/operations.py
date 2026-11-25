"""The operations of a write (docs/spec.md, Writes): what a batch can ask the vault to do.

An operation is one change the user or an agent asks for: set or remove a leaf, move, trash,
convert, rename, merge, create, restore, rename or delete a folder, rename an attachment, save
a section of the tool note, or set the URIs of logins. The models here
only say what an operation looks like and which items it touches. The write path
(writes/path.py) checks and applies them, the queue (queue/) stores them as their
model_dump until they are approved. Nothing here puts a value into an error message.
"""

from typing import Annotated, Any, Literal, assert_never

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError

from bwtidy.items.coordinates import URIS, Coordinate, UriMatch, Value, uri_entries
from bwtidy.sections import Section, SectionChange
from bwtidy.validation import safe_message

FieldType = Literal[0, 1, 2]


class SetLeaf(BaseModel):
    kind: Literal["setLeaf"]
    itemId: str
    revisionDate: str
    column: str
    value: Value
    fieldType: FieldType | None = None
    ## the favorite flag the grid showed, for a setLeaf of favorite only (see shown)
    shownFavorite: bool | None = None


class RemoveLeaf(BaseModel):
    kind: Literal["removeLeaf"]
    itemId: str
    revisionDate: str
    column: str


class GenerateLeaf(BaseModel):
    """A value from bw's generator for login.password or a hidden field, made at write time
    with the item's generator setting (else the default). Nobody sees it until the user reveals
    it in the grid."""

    kind: Literal["generateLeaf"]
    itemId: str
    revisionDate: str
    column: str


class MoveToFolder(BaseModel):
    kind: Literal["moveToFolder"]
    itemIds: list[str]
    revisionDates: dict[str, str]
    folderName: str
    ## the folder id the grid showed for each item, None for no folder (see shown)
    shownFolderIds: dict[str, str | None] | None = None


class SetCollections(BaseModel):
    kind: Literal["setCollections"]
    itemId: str
    revisionDate: str
    collectionNames: list[str]
    ## the collection ids the grid showed (see shown)
    shownCollectionIds: list[str] | None = None


class MoveToOrganization(BaseModel):
    kind: Literal["moveToOrganization"]
    itemId: str
    revisionDate: str
    organizationName: str
    collectionNames: list[str]


class MoveToPersonal(BaseModel):
    kind: Literal["moveToPersonal"]
    itemId: str
    revisionDate: str


class TrashItem(BaseModel):
    kind: Literal["trashItem"]
    itemId: str
    revisionDate: str


class ConvertItem(BaseModel):
    kind: Literal["convertItem"]
    itemId: str
    revisionDate: str
    newType: int
    mapping: dict[str, str]


class ApplyTemplate(BaseModel):
    kind: Literal["applyTemplate"]
    itemId: str
    revisionDate: str
    template: str
    mapping: dict[str, str]


class ItemRevision(BaseModel):
    itemId: str
    revisionDate: str


class RenameField(BaseModel):
    ## from_ is built by its name in code, the JSON shape says "from"
    model_config = ConfigDict(populate_by_name=True)

    kind: Literal["renameField"]
    to: str
    items: list[ItemRevision]
    ## "from" is a Python keyword
    from_: str = Field(alias="from")


class MergeColumns(BaseModel):
    kind: Literal["mergeColumns"]
    sources: list[str]
    target: str
    items: list[ItemRevision]
    resolutions: dict[str, str] = {}


class MergeItems(BaseModel):
    kind: Literal["mergeItems"]
    fromItemId: str
    fromRevisionDate: str
    toItemId: str
    toRevisionDate: str
    mapping: dict[str, str]
    ## target columns (lists) that get the source's entries added instead of replaced
    append: list[str] = []


class UriEntry(BaseModel):
    uri: str
    ## Bitwarden's match setting (coordinates.URI_MATCHES), None for the client's default
    match: UriMatch = None


class ItemUris(BaseModel):
    itemId: str
    revisionDate: str
    ## the item's URIs as the proposer was shown them, a conflict when the item has others now
    shownUris: list[UriEntry]
    uris: list[UriEntry]


class SetUris(BaseModel):
    """The whole URI list of each login, with the match setting of every URI, so an agent can
    fix login addresses that autofill on the wrong site."""

    kind: Literal["setUris"]
    items: list[ItemUris]


def uri_pairs(uris: list[UriEntry]) -> list[tuple[str, UriMatch]]:
    return [(entry.uri, entry.match) for entry in uris]


class RestoreItems(BaseModel):
    kind: Literal["restoreItems"]
    backup: str
    itemIds: list[str]
    ## the revisionDate of every item that was active in the vault when the restore was planned
    revisionDates: dict[str, str]
    ## the change log row the plan was made for, kept so the restore's own row can name it
    changeLogId: int | None = None


class CreateItem(BaseModel):
    """A new item from a raw type or a template (exactly one of them).

    values never holds a secret. secretValues is the only way a secret value comes in: only a
    person sends it, and the queue stores it encrypted.
    """

    kind: Literal["createItem"]
    name: str
    type: int | None = None
    template: str | None = None
    folderName: str = ""
    organizationName: str = ""
    collectionNames: list[str] = []
    values: dict[str, Value] = {}
    secretValues: dict[str, str] = {}
    generatePassword: bool = False
    generateColumns: list[str] = []


class RenameFolder(BaseModel):
    """A new name for a folder. Its items stay in it, none of them is written."""

    kind: Literal["renameFolder"]
    folderId: str
    ## the folder's name as the client showed it, a conflict when the folder has another now
    shownName: str
    newName: str


class DeleteFolder(BaseModel):
    """The delete of an empty folder. One that holds an item is refused, also at write time."""

    kind: Literal["deleteFolder"]
    folderId: str
    shownName: str


class RestoreFolder(BaseModel):
    """A folder put back as a backup holds it: renamed back, or created again when it is gone."""

    kind: Literal["restoreFolder"]
    backup: str
    folderId: str
    ## the change log row the plan was made for, like a restoreItems
    changeLogId: int | None = None


class RenameAttachment(BaseModel):
    """A new file name for an attachment of an item. bwtidy downloads it, uploads it again
    under the new name and deletes the old one (bw serve cannot rename one in place), so the
    attachment gets a new id. Its contents never leave the write path."""

    kind: Literal["renameAttachment"]
    itemId: str
    revisionDate: str
    attachmentId: str
    fileName: str
    ## the attachment's name as the proposer saw it, a conflict when it has another now
    shownFileName: str


class SetToolNoteSection(BaseModel):
    """A save of one section of the tool note's notes: its changes, applied to the section one
    by one (changed in the section's module under sections/) at write time, on the note as it
    is then. Only bwtidy's own code queues it, every caller from outside is refused like any
    other operation on the tool note.
    """

    kind: Literal["setToolNoteSection"]
    section: Section
    changes: list[SectionChange]


type Operation = (
    SetLeaf
    | RemoveLeaf
    | GenerateLeaf
    | MoveToFolder
    | SetCollections
    | MoveToOrganization
    | MoveToPersonal
    | TrashItem
    | ConvertItem
    | ApplyTemplate
    | RenameField
    | MergeColumns
    | MergeItems
    | SetUris
    | RestoreItems
    | CreateItem
    | RenameFolder
    | DeleteFolder
    | RestoreFolder
    | RenameAttachment
    | SetToolNoteSection
)


OPERATIONS = TypeAdapter(list[Annotated[Operation, Field(discriminator="kind")]])


class InvalidOperation(Exception):
    """An operation is malformed or not allowed. Nothing was written.

    index is the position of the operation in the batch when one operation is to blame, and
    reason the problem without the position. secret_rule is true when the operation broke the
    secret rule of a setLeaf (SecretIntoPlain), so a caller can tell it apart without reading
    the text.
    """

    def __init__(
        self,
        message: str,
        index: int | None = None,
        reason: str | None = None,
        secret_rule: bool = False,
    ) -> None:
        super().__init__(message)
        self.index = index
        self.reason = message if reason is None else reason
        self.secret_rule = secret_rule


class Conflict(Exception):
    """An item or a folder changed since the client loaded it. Nothing was written.

    A conflict is {itemId, expected, actual} (plus column for a shown value), a folder's
    {folderId, expected, actual} with the names, actual None for a folder that is gone.
    """

    def __init__(self, conflicts: list[dict[str, Any]]) -> None:
        count = len(conflicts)
        folders = all("folderId" in conflict for conflict in conflicts)
        one, many = ("folder", "folders") if folders else ("item", "items")
        noun = one if count == 1 else many
        super().__init__(f"{count} {noun} changed since the grid was loaded, nothing was written")
        self.conflicts = conflicts


class MergeConflicts(Exception):
    """Items hold values in more than one merged column and no resolution says which to keep."""

    def __init__(self, conflicts: list[dict[str, Any]]) -> None:
        count = len(conflicts)
        noun = "item has" if count == 1 else "items have"
        super().__init__(
            f"{count} {noun} more than one of the merged columns, choose which to keep. "
            "Nothing was written."
        )
        self.conflicts = conflicts


def parse_operations(payload: Any) -> list[Operation]:
    """The operations of a request body. Raises InvalidOperation with a readable message."""
    if not isinstance(payload, list) or not payload:
        raise InvalidOperation("operations must be a non-empty list")
    try:
        return OPERATIONS.validate_python(payload)
    except ValidationError as error:
        problems = [
            f"operations[{'.'.join(str(p) for p in e['loc'])}]: {safe_message(e)}"
            for e in error.errors()
        ]
        raise InvalidOperation("; ".join(problems[:5])) from None


def touched(op: Operation) -> dict[str, str]:
    """The items an operation touches, with the revisionDate the client saw for each."""
    if isinstance(op, MoveToFolder):
        return {item_id: op.revisionDates.get(item_id, "") for item_id in op.itemIds}
    if isinstance(op, RenameField | MergeColumns | SetUris):
        return {item.itemId: item.revisionDate for item in op.items}
    if isinstance(op, MergeItems):
        return {op.fromItemId: op.fromRevisionDate, op.toItemId: op.toRevisionDate}
    if isinstance(op, RestoreItems):
        ## only the items active when the restore was planned have a revisionDate to check
        return {i: op.revisionDates[i] for i in op.itemIds if i in op.revisionDates}
    if isinstance(op, CreateItem | SetToolNoteSection):
        return {}
    if isinstance(op, RenameFolder | DeleteFolder | RestoreFolder):
        return {}
    return {op.itemId: op.revisionDate}


def shown_folder(op: Operation) -> dict[str, str]:
    """The folder a rename or delete is about, with the name the client showed: {folderId: name}.

    A folder has no revisionDate, so its name is what tells a change made elsewhere.
    """
    if isinstance(op, RenameFolder | DeleteFolder):
        return {op.folderId: op.shownName}
    return {}


def folder_of(op: Operation) -> str | None:
    """The id of the folder a change log row of the operation records, None for the item kinds."""
    if isinstance(op, RenameFolder | DeleteFolder | RestoreFolder):
        return op.folderId
    return None


FOLDER = "folder"
FAVORITE = "favorite"
COLLECTIONS = "collections"
ATTACHMENTS = "attachments"


def shown_attachment(op: Operation) -> dict[str, tuple[str, str]]:
    """The attachment a rename is about, by item id: its id and the name the proposer saw.

    Adding or deleting an attachment leaves the item's revisionDate as it is on Vaultwarden, so
    only the name tells a rename made elsewhere (docs/spec.md, Writes, renameAttachment).
    """
    if isinstance(op, RenameAttachment):
        return {op.itemId: (op.attachmentId, op.shownFileName)}
    return {}


def attachment_of(item: dict[str, Any], attachment_id: str) -> dict[str, Any] | None:
    """The attachment of the item with this id, None when it has none."""
    for attachment in item.get("attachments") or []:
        if isinstance(attachment, dict) and attachment.get("id") == attachment_id:
            return attachment
    return None


def shown(op: Operation) -> dict[str, tuple[str, Any]]:
    """What the grid showed of a value another client changes without a new revisionDate, by
    item id: the column (folder, favorite or collections) and the value, the folder as its id
    or None, the collections as a set of ids. A setUris carries the URIs the proposer was
    shown (login.uris, a tuple of (uri, match) pairs).

    A partial update of the Bitwarden clients (folder, favorite) and Vaultwarden's collections
    leave the revisionDate as it is, so only these values tell such a change made elsewhere
    (docs/spec.md, Writes). An operation without them is checked by its revisionDates only.
    The URIs change with a new revisionDate, but bwtidy's own write of them follows the
    revisionDate's chain, and a whole new list would undo it.
    """
    if isinstance(op, MoveToFolder) and op.shownFolderIds is not None:
        return {i: (FOLDER, folder or None) for i, folder in op.shownFolderIds.items()}
    if isinstance(op, SetLeaf) and op.shownFavorite is not None and op.column == FAVORITE:
        return {op.itemId: (FAVORITE, op.shownFavorite)}
    if isinstance(op, SetCollections) and op.shownCollectionIds is not None:
        return {op.itemId: (COLLECTIONS, frozenset(op.shownCollectionIds))}
    if isinstance(op, SetUris):
        return {i.itemId: (URIS, tuple(uri_pairs(i.shownUris))) for i in op.items}
    return {}


def shown_now(item: dict[str, Any], column: str) -> Any:
    """The value of item in a column of shown, in the same form."""
    if column == FOLDER:
        return item.get("folderId") or None
    if column == FAVORITE:
        return bool(item.get("favorite"))
    if column == URIS:
        return tuple(uri_entries(item))
    return frozenset(item.get("collectionIds") or [])


def item_ids(op: Operation) -> list[str]:
    """The item ids a change log row records for the operation."""
    if isinstance(op, RestoreItems):
        return list(op.itemIds)
    return list(touched(op))


ORGANIZATION = "organization"


def _from_to(mapping: dict[str, str]) -> list[str]:
    """The source and the target of each pair of a mapping {target: source} that moves a value."""
    return [
        column
        for target, source in mapping.items()
        if source != target
        for column in (source, target)
    ]


def columns(op: Operation) -> list[str]:
    """The coordinates a change log row records for the operation, as the grid names them.

    A coordinate is never a value, but a custom field's name is vault content, recorded since
    knowing what changed is worth it (docs/spec.md, Writes). Whole-item kinds
    (trash, create, restore, a section of the tool note) record none, the folder kinds folder,
    a merge of items the columns of the item it keeps that take a value of the other (that one
    goes to the trash). A conversion and a template also write coordinates their item decides,
    which the write path adds (writes/path.py).
    """
    if isinstance(op, SetLeaf | RemoveLeaf | GenerateLeaf):
        found = [op.column]
    elif isinstance(op, MoveToFolder | RenameFolder | DeleteFolder | RestoreFolder):
        found = [FOLDER]
    elif isinstance(op, SetCollections):
        found = [COLLECTIONS]
    elif isinstance(op, MoveToOrganization | MoveToPersonal):
        found = [ORGANIZATION, COLLECTIONS]
    elif isinstance(op, RenameAttachment):
        found = [ATTACHMENTS]
    elif isinstance(op, ConvertItem):
        found = ["type", *_from_to(op.mapping)]
    elif isinstance(op, ApplyTemplate):
        found = _from_to(op.mapping)
    elif isinstance(op, RenameField):
        found = [Coordinate.field(op.from_).display, Coordinate.field(op.to).display]
    elif isinstance(op, MergeColumns):
        found = [*op.sources, op.target]
    elif isinstance(op, MergeItems):
        found = list(op.mapping)
    elif isinstance(op, SetUris):
        found = [URIS]
    elif isinstance(op, TrashItem | RestoreItems | CreateItem | SetToolNoteSection):
        found = []
    else:
        assert_never(op)
    return list(dict.fromkeys(found))


def refreshed(
    op: Operation, items: dict[str, dict[str, Any]], folders: dict[str, str]
) -> Operation:
    """op with every revisionDate and every shown value it carries taken from items and folders
    (the vault as it is now, folders the folder names by id), so its write goes over a change
    made elsewhere since it was queued. Nothing else of op changes. Every item touched(op) names
    must be in items, every folder shown_folder(op) names in folders.
    """

    def revision(item_id: str) -> str:
        return items[item_id]["revisionDate"]

    def dates(revisions: dict[str, str]) -> dict[str, str]:
        return {i: revision(i) if i in items else date for i, date in revisions.items()}

    if isinstance(op, MoveToFolder):
        update: dict[str, Any] = {"revisionDates": dates(op.revisionDates)}
        if op.shownFolderIds is not None:
            update["shownFolderIds"] = {
                i: shown_now(items[i], FOLDER) for i in op.shownFolderIds if i in items
            }
    elif isinstance(op, RenameField | MergeColumns):
        rows = [ItemRevision(itemId=i.itemId, revisionDate=revision(i.itemId)) for i in op.items]
        update = {"items": rows}
    elif isinstance(op, MergeItems):
        update = {
            "fromRevisionDate": revision(op.fromItemId),
            "toRevisionDate": revision(op.toItemId),
        }
    elif isinstance(op, SetUris):
        rows = [
            i.model_copy(
                update={
                    "revisionDate": revision(i.itemId),
                    "shownUris": [
                        UriEntry(uri=uri, match=match)
                        for uri, match in uri_entries(items[i.itemId])
                    ],
                }
            )
            for i in op.items
        ]
        update = {"items": rows}
    elif isinstance(op, RestoreItems):
        update = {"revisionDates": dates(op.revisionDates)}
    elif isinstance(op, RenameFolder | DeleteFolder):
        update = {"shownName": folders[op.folderId]}
    elif isinstance(op, CreateItem | SetToolNoteSection | RestoreFolder):
        update = {}
    elif isinstance(op, SetLeaf):
        update = {"revisionDate": revision(op.itemId)}
        if op.shownFavorite is not None:
            update["shownFavorite"] = shown_now(items[op.itemId], FAVORITE)
    elif isinstance(op, SetCollections):
        update = {"revisionDate": revision(op.itemId)}
        if op.shownCollectionIds is not None:
            update["shownCollectionIds"] = sorted(shown_now(items[op.itemId], COLLECTIONS))
    elif isinstance(op, RenameAttachment):
        update = {"revisionDate": revision(op.itemId)}
        attachment = attachment_of(items[op.itemId], op.attachmentId)
        if attachment is not None:
            update["shownFileName"] = attachment.get("fileName") or ""
    elif isinstance(
        op,
        RemoveLeaf
        | GenerateLeaf
        | MoveToOrganization
        | MoveToPersonal
        | TrashItem
        | ConvertItem
        | ApplyTemplate,
    ):
        update = {"revisionDate": revision(op.itemId)}
    else:
        assert_never(op)
    return op.model_copy(update=update)

"""The propose tools: each builds one operation from what the agent sees and adds it to the review
queue through `AgentVault.propose`. None of them writes to the vault."""

import fnmatch
from typing import Annotated, Literal

from mcp.server.mcpserver.exceptions import ToolError
from pydantic import BaseModel, Field

from bwtidy.items.coordinates import URI_MATCHES, Coordinate, Value, parse
from bwtidy.mcp.texts import ItemId
from bwtidy.mcp.vault import AgentVault, Visible, dump
from bwtidy.operations import (
    ApplyTemplate,
    ConvertItem,
    CreateItem,
    DeleteFolder,
    ItemRevision,
    ItemUris,
    MergeColumns,
    MergeItems,
    MoveToFolder,
    MoveToOrganization,
    MoveToPersonal,
    RenameAttachment,
    RenameField,
    RenameFolder,
    SetCollections,
    SetLeaf,
    SetUris,
    TrashItem,
    UriEntry,
)
from bwtidy.queue.view import TYPE_WORDS, plural, summarize_create, summarize_rename_attachment
from bwtidy.sections.matching import find_template

## the item type words propose_create_item takes
CREATE_TYPES = {"login": 1, "secureNote": 2, "card": 3, "identity": 4, "sshKey": 5}

## what propose_create_item answers for an SSH key, which needs a private key MCP cannot give
SSH_KEY_REFUSAL = (
    "an SSH key cannot be created through MCP: its private key is a secret value and MCP never"
    " takes one. Use propose_convert_item on an item that holds the key (for example a secure"
    " note with the key in notes), mapping its private key from that item's column"
)

ItemIds = Annotated[list[str], Field(description="Item ids from list_items")]
Reason = Annotated[
    str | None, Field(description="Why the change helps, shown to the user with the proposal")
]
## the words for Bitwarden's URI match settings, by number
MATCH_NUMBERS = {word: number for number, word in URI_MATCHES.items()}
MatchWord = Literal["domain", "host", "startsWith", "exact", "regex", "never"]


class UriInput(BaseModel):
    uri: Annotated[str, Field(description="The URI, not empty")]
    match: Annotated[
        MatchWord | None,
        Field(description="domain, host, startsWith, exact, regex, never, or null for default"),
    ] = None


Mapping = Annotated[
    dict[str, str],
    Field(description="{target column: source column}, columns as list_items names them"),
]


def _quoted(item: Visible) -> str:
    return f'"{item.name}"'


async def propose_move_to_folder(
    vault: AgentVault,
    item_ids: ItemIds,
    folder: Annotated[str, Field(description="Folder name, empty for no folder")],
    reason: Reason = None,
) -> str:
    items = [vault.item(i) for i in item_ids]
    op = MoveToFolder(
        kind="moveToFolder",
        itemIds=item_ids,
        revisionDates={i.id: i.revision() for i in items},
        folderName=folder,
        ## the folders as the agent sees them now, so a move made elsewhere before the user
        ## approves is a conflict
        shownFolderIds={i.id: i.folder_id for i in items},
    )
    which = _quoted(items[0]) if len(items) == 1 else plural(len(items), "item", "items")
    where = f'to folder "{folder}"' if folder else "out of its folder"
    return dump(vault.propose(op, f"Move {which} {where}", reason))


async def propose_rename_folder(
    vault: AgentVault,
    folder: Annotated[str, Field(description="The folder's name as list_folders names it")],
    new_name: Annotated[str, Field(description="The new name")],
    reason: Reason = None,
) -> str:
    folder_id = vault.folder(folder)
    op = RenameFolder(kind="renameFolder", folderId=folder_id, shownName=folder, newName=new_name)
    summary = f'Rename the folder "{folder}" to "{new_name}"'
    return dump(vault.propose(op, summary, reason))


async def propose_delete_folder(
    vault: AgentVault,
    folder: Annotated[str, Field(description="The folder's name as list_folders names it")],
    reason: Reason = None,
) -> str:
    folder_id = vault.folder(folder)
    op = DeleteFolder(kind="deleteFolder", folderId=folder_id, shownName=folder)
    return dump(vault.propose(op, f'Delete the folder "{folder}"', reason))


async def propose_set_collections(
    vault: AgentVault,
    item_id: ItemId,
    collections: Annotated[list[str], Field(description="Collection names, at least one")],
    reason: Reason = None,
) -> str:
    item = vault.item(item_id)
    op = SetCollections(
        kind="setCollections",
        itemId=item_id,
        revisionDate=item.revision(),
        collectionNames=collections,
        shownCollectionIds=item.collection_ids,
    )
    summary = f"Set the collections of {_quoted(item)} to {', '.join(collections)}"
    return dump(vault.propose(op, summary, reason))


async def propose_move_to_organization(
    vault: AgentVault,
    item_id: ItemId,
    organization: Annotated[str, Field(description="Organization name")],
    collections: Annotated[
        list[str], Field(description="Collection names of that organization, at least one")
    ],
    reason: Reason = None,
) -> str:
    item = vault.item(item_id)
    op = MoveToOrganization(
        kind="moveToOrganization",
        itemId=item_id,
        revisionDate=item.revision(),
        organizationName=organization,
        collectionNames=collections,
    )
    summary = (
        f'Move {_quoted(item)} to organization "{organization}"'
        f" (collections {', '.join(collections)})"
    )
    return dump(vault.propose(op, summary, reason))


async def propose_move_to_personal(
    vault: AgentVault, item_id: ItemId, reason: Reason = None
) -> str:
    item = vault.item(item_id)
    op = MoveToPersonal(kind="moveToPersonal", itemId=item_id, revisionDate=item.revision())
    return dump(vault.propose(op, f"Move {_quoted(item)} to the personal vault", reason))


async def propose_rename_item(
    vault: AgentVault,
    item_id: ItemId,
    name: Annotated[str, Field(description="The new name")],
    reason: Reason = None,
) -> str:
    item = vault.item(item_id)
    op = SetLeaf(
        kind="setLeaf",
        itemId=item_id,
        revisionDate=item.revision(),
        column="name",
        value=name,
    )
    return dump(vault.propose(op, f'Rename {_quoted(item)} to "{name}"', reason))


def _item_uris(item: Visible, uris: list[UriEntry]) -> ItemUris:
    shown = [UriEntry(uri=uri, match=match) for uri, match in item.uris]
    return ItemUris(itemId=item.id, revisionDate=item.revision(), shownUris=shown, uris=uris)


async def propose_set_uris(
    vault: AgentVault,
    item_id: ItemId,
    uris: Annotated[list[UriInput], Field(description="The login's new URIs, each {uri, match}")],
    reason: Reason = None,
) -> str:
    item = vault.item(item_id)
    wanted = [
        UriEntry(uri=u.uri, match=None if u.match is None else MATCH_NUMBERS[u.match]) for u in uris
    ]
    op = SetUris(kind="setUris", items=[_item_uris(item, wanted)])
    summary = f"Set the URIs of {_quoted(item)} ({len(wanted)})"
    return dump(vault.propose(op, summary, reason))


async def propose_set_uri_match(
    vault: AgentVault,
    item_ids: ItemIds,
    match: Annotated[
        MatchWord | None,
        Field(description="domain, host, startsWith, exact, regex, never, or null for default"),
    ],
    uri_pattern: Annotated[
        str | None,
        Field(description="A shell glob over the whole URI, ignoring case, default every URI"),
    ] = None,
    reason: Reason = None,
) -> str:
    number = None if match is None else MATCH_NUMBERS[match]
    pattern = None if uri_pattern is None else uri_pattern.casefold()
    changed = 0
    rows = []
    for item in [vault.item(i) for i in dict.fromkeys(item_ids)]:
        uris = []
        for uri, now in item.uris:
            hit = pattern is None or fnmatch.fnmatchcase(uri.casefold(), pattern)
            if hit and now != number:
                changed += 1
                now = number
            uris.append(UriEntry(uri=uri, match=now))
        if [(u.uri, u.match) for u in uris] != item.uris:
            rows.append(_item_uris(item, uris))
    if not rows:
        raise ToolError("no URI of these items would change, nothing was proposed")
    op = SetUris(kind="setUris", items=rows)
    word = match or "default"
    summary = (
        f"Set URI match {word} on {plural(changed, 'URI', 'URIs')} of"
        f" {plural(len(rows), 'item', 'items')}"
    )
    return dump(vault.propose(op, summary, reason))


async def propose_rename_field(
    vault: AgentVault,
    from_name: Annotated[str, Field(description="The field name now, verbatim")],
    to_name: Annotated[str, Field(description="The new field name")],
    item_ids: Annotated[
        list[str] | None,
        Field(description="Only these items, default every item with the field"),
    ] = None,
    reason: Reason = None,
) -> str:
    column = Coordinate.field(from_name)
    if item_ids is None:
        items = [i for i in vault.items() if column in i.columns]
        if not items:
            raise ToolError(f"no item has a field named {from_name!r}")
    else:
        items = [vault.item(i) for i in item_ids]
    op = RenameField(
        kind="renameField",
        from_=from_name,
        to=to_name,
        items=[ItemRevision(itemId=i.id, revisionDate=i.revision()) for i in items],
    )
    summary = f'Rename field "{from_name}" to "{to_name}" in {plural(len(items), "item", "items")}'
    return dump(vault.propose(op, summary, reason))


async def propose_merge_columns(
    vault: AgentVault,
    sources: Annotated[
        list[str], Field(description="Source columns, like fields.User or login.username")
    ],
    target: Annotated[str, Field(description="The column that keeps the value")],
    item_ids: Annotated[
        list[str] | None,
        Field(description="Only these items, default every item with a source column"),
    ] = None,
    resolutions: Annotated[
        dict[str, str] | None,
        Field(description="{item id: the merged column whose value to keep}"),
    ] = None,
    reason: Reason = None,
) -> str:
    if item_ids is None:
        try:
            columns = [parse(s) for s in sources if s != target]
        except ValueError as error:
            raise ToolError(str(error)) from None
        items = [i for i in vault.items() if any(c in i.columns for c in columns)]
        if not items:
            raise ToolError("no item has any of the source columns")
    else:
        items = [vault.item(i) for i in item_ids]
    for item_id in resolutions or {}:
        vault.item(item_id)
    op = MergeColumns(
        kind="mergeColumns",
        sources=sources,
        target=target,
        items=[ItemRevision(itemId=i.id, revisionDate=i.revision()) for i in items],
        resolutions=resolutions or {},
    )
    summary = f"Merge {', '.join(sources)} into {target} in {plural(len(items), 'item', 'items')}"
    return dump(vault.propose(op, summary, reason))


async def propose_apply_template(
    vault: AgentVault,
    item_id: ItemId,
    template: Annotated[str, Field(description="Template name from list_templates")],
    mapping: Annotated[
        dict[str, str] | None,
        Field(description="{template column: the item's column}, default none"),
    ] = None,
    reason: Reason = None,
) -> str:
    item = vault.item(item_id)
    op = ApplyTemplate(
        kind="applyTemplate",
        itemId=item_id,
        revisionDate=item.revision(),
        template=template,
        mapping=mapping or {},
    )
    summary = f'Apply template "{template}" to {_quoted(item)}'
    return dump(vault.propose(op, summary, reason))


async def propose_convert_item(
    vault: AgentVault,
    item_id: ItemId,
    type: Annotated[
        str, Field(description="The new type: login, secureNote, card, identity or sshKey")
    ],
    mapping: Annotated[
        dict[str, str] | None,
        Field(description="{target column: the item's column}, default none"),
    ] = None,
    reason: Reason = None,
) -> str:
    new_type = CREATE_TYPES.get(type)
    if new_type is None:
        raise ToolError(f"unknown type {type!r}, use one of {', '.join(CREATE_TYPES)}")
    item = vault.item(item_id)
    op = ConvertItem(
        kind="convertItem",
        itemId=item_id,
        revisionDate=item.revision(),
        newType=new_type,
        mapping=mapping or {},
    )
    summary = f"Convert {_quoted(item)} to {TYPE_WORDS[new_type]}"
    if op.mapping:
        moves = ", ".join(f"{source} to {target}" for target, source in op.mapping.items())
        summary = f"{summary} ({moves})"
    return dump(vault.propose(op, summary, reason))


async def propose_merge_items(
    vault: AgentVault,
    from_item_id: Annotated[str, Field(description="The item that goes to the trash")],
    to_item_id: Annotated[str, Field(description="The item that receives the values")],
    mapping: Mapping,
    reason: Reason = None,
) -> str:
    source = vault.item(from_item_id)
    target = vault.item(to_item_id)
    op = MergeItems(
        kind="mergeItems",
        fromItemId=from_item_id,
        fromRevisionDate=source.revision(),
        toItemId=to_item_id,
        toRevisionDate=target.revision(),
        mapping=mapping,
    )
    summary = f"Merge {_quoted(source)} into {_quoted(target)} and trash {_quoted(source)}"
    return dump(vault.propose(op, summary, reason))


async def propose_rename_attachment(
    vault: AgentVault,
    item_id: ItemId,
    attachment_id: Annotated[
        str, Field(description="The attachment's id from list_items or get_item")
    ],
    new_name: Annotated[str, Field(description="The new file name")],
    reason: Reason = None,
) -> str:
    item = vault.item(item_id)
    attachment = next((a for a in item.attachments if a["id"] == attachment_id), None)
    if attachment is None:
        raise ToolError(f"item {item_id} has no attachment with the id {attachment_id!r}")
    op = RenameAttachment(
        kind="renameAttachment",
        itemId=item_id,
        revisionDate=item.revision(),
        attachmentId=attachment_id,
        fileName=new_name,
        ## the name as the agent sees it now, so a rename made elsewhere before the user
        ## approves is a conflict
        shownFileName=attachment.get("fileName") or "",
    )
    summary = summarize_rename_attachment(op, {"name": item.name})
    return dump(vault.propose(op, summary, reason))


async def propose_trash_item(vault: AgentVault, item_id: ItemId, reason: Reason = None) -> str:
    item = vault.item(item_id)
    op = TrashItem(kind="trashItem", itemId=item_id, revisionDate=item.revision())
    return dump(vault.propose(op, f"Move {_quoted(item)} to the trash", reason))


async def propose_create_item(
    vault: AgentVault,
    name: Annotated[str, Field(description="The new item's name")],
    type: Annotated[
        str | None,
        Field(description="login, secureNote, card or identity, when no template"),
    ] = None,
    template: Annotated[
        str | None, Field(description="Template name from list_templates, when no type")
    ] = None,
    folder: Annotated[
        str, Field(description="Folder name, created when missing, empty for none")
    ] = "",
    organization: Annotated[
        str, Field(description="Organization name, empty for the personal vault")
    ] = "",
    collections: Annotated[
        list[str] | None,
        Field(
            description="Collection names of the organization, at least one when an"
            " organization is given"
        ),
    ] = None,
    values: Annotated[
        dict[str, Value] | None,
        Field(description="{column: value} for non-secret columns, default none"),
    ] = None,
    generate_password: Annotated[
        bool,
        Field(
            description="A login only: bw generates its password at write time, following"
            " the default generator settings"
        ),
    ] = False,
    generate_secrets: Annotated[
        list[str] | None,
        Field(
            description="Secret columns bw generates at write time, login.password or hidden"
            " fields, which nobody can read through MCP"
        ),
    ] = None,
    reason: Reason = None,
) -> str:
    item_type = None
    if type is not None:
        item_type = CREATE_TYPES.get(type)
        if item_type is None:
            raise ToolError(f"unknown type {type!r}, use one of {', '.join(CREATE_TYPES)}")
    stored = find_template(vault.templates()[0], template) if template is not None else None
    if item_type == CREATE_TYPES["sshKey"] or (stored is not None and stored.type == 5):
        raise ToolError(SSH_KEY_REFUSAL)
    op = CreateItem(
        kind="createItem",
        name=name,
        type=item_type,
        template=template,
        folderName=folder,
        organizationName=organization,
        collectionNames=collections or [],
        values=values or {},
        generatePassword=generate_password,
        generateColumns=generate_secrets or [],
    )
    return dump(vault.propose(op, summarize_create(op), reason))

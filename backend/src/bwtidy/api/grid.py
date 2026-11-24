"""The vault as a grid of coordinates, its near-duplicate columns, and the one secret route."""

from collections import Counter, defaultdict
from typing import Annotated, Any

from fastapi import APIRouter, HTTPException, Query, Request, Response
from pydantic import BaseModel

from bwtidy.api.app_state import app_state
from bwtidy.api.deps import Session
from bwtidy.api.sections import own_generator_ids
from bwtidy.items.coordinates import (
    BOOLEAN_FIELD,
    HIDDEN_FIELD,
    LINKED_FIELD,
    MASKED,
    MULTI_PATHS,
    READ_ONLY_PATHS,
    SECRET_PATHS,
    TEXT_FIELD,
    Coordinate,
    Value,
    field_entries,
    field_types,
    flatten,
    is_secret,
    parse,
    sort_key,
)
from bwtidy.items.normalize import near_duplicate_groups
from bwtidy.queue.to_fill import RefusedMark, secret_slot
from bwtidy.vault import ToolNoteId, VaultCache, tool_note_id

router = APIRouter(prefix="/api")

## flatten leaves out false booleans, so without these a vault with no favorite has no column
## to make one in
ALWAYS_COLUMNS = (Coordinate.at("favorite"), Coordinate.at("reprompt"))


def column(coordinate: Coordinate, secret: bool) -> dict[str, Any]:
    display = coordinate.display
    return {
        "id": display,
        "label": display,
        "group": coordinate.group,
        "secret": secret,
        "multi": coordinate.kind == "path" and coordinate.path in MULTI_PATHS,
        "readOnly": coordinate.kind == "path" and coordinate.path in READ_ONLY_PATHS,
    }


def vault_fields(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Every custom field name the items have, empty values included, with its field type.

    The template editor offers these. Hidden wins when items disagree (the secret rule), else
    the most common type. A name that is only ever a linked field is left out, a template
    field cannot be linked.
    """
    types: defaultdict[str, Counter[int]] = defaultdict(Counter)
    for item in items:
        for entry in item.get("fields") or []:
            field_type = entry.get("type", 0)
            if field_type != LINKED_FIELD:
                types[entry.get("name") or ""][field_type] += 1
    fields = []
    for name in sorted(types, key=lambda n: (n.casefold(), n)):
        counts = types[name]
        common = min(counts, key=lambda field_type: (-counts[field_type], field_type))
        fields.append({"name": name, "fieldType": HIDDEN_FIELD if counts[HIDDEN_FIELD] else common})
    return fields


def empty_secrets(item: dict[str, Any]) -> list[str]:
    """The secret columns the item has but holds no value in, the ones that can be marked to
    fill in."""
    coordinates = [Coordinate.at(path) for path in sorted(SECRET_PATHS)]
    coordinates += [coordinate for coordinate, _ in field_entries(item)]
    return [c.display for c in sorted(coordinates, key=sort_key) if secret_slot(item, c)]


def vault_organizations(cache: VaultCache) -> list[dict[str, Any]]:
    """Every organization of the vault with all its collections, also those no item is in."""
    return [
        {
            "name": organization.get("name") or "",
            "collections": sorted(
                (
                    collection.get("name") or ""
                    for collection in cache.collections
                    if collection.get("id")
                    and collection.get("organizationId") == organization["id"]
                ),
                key=str.casefold,
            ),
        }
        for organization in cache.organizations
        if organization.get("id")
    ]


def vault_folders(cache: VaultCache) -> list[dict[str, Any]]:
    """Every folder of the vault by name, also those no item is in, with the number of items in
    it (items) and of items in the trash still in it (trashedItems), for Manage folders."""
    active = Counter(item.get("folderId") for item in cache.items)
    trashed = Counter(item.get("folderId") for item in cache.trashed)
    return [
        {
            "id": folder["id"],
            "name": folder.get("name") or "",
            "items": active.get(folder["id"], 0),
            "trashedItems": trashed.get(folder["id"], 0),
        }
        for folder in sorted(cache.folders, key=lambda f: (f.get("name") or "").casefold())
        if folder.get("id")
    ]


def empty_text_fields(item: dict[str, Any], values: dict[Coordinate, Value]) -> list[str]:
    """The item's text fields without a value (values is the item flattened). They have no cell,
    and in a secret column the dialog must not take them for new fields, which become hidden."""
    return [
        coordinate.display
        for coordinate, entry in field_entries(item)
        if entry.get("type", 0) == TEXT_FIELD and coordinate not in values
    ]


def fields_of_type(item: dict[str, Any], field_type: int) -> list[str]:
    """The item's field columns of one type, with or without a value. A secret column can hold a
    boolean or linked field on its own item, and its dialog must not take it for a hidden one."""
    return [
        coordinate.display
        for coordinate, entry in field_entries(item)
        if entry.get("type", 0) == field_type
    ]


def build_grid(
    cache: VaultCache,
    remembered: ToolNoteId,
    to_fill: dict[str, list[str]] | None = None,
    own_generator: set[str] | None = None,
) -> dict[str, Any]:
    """Columns and rows, toolNoteId (the row the grid shows read-only), the vault's fields, its
    organizations and its folders.

    A column comes from the values of the items, so a column on the to-fill list is added here:
    its cell may be the only one of that name, with no value yet to fill in. A row whose item id
    is in own_generator carries ownGenerator.
    """
    lookups = cache.lookups()
    secret_columns: dict[Coordinate, bool] = dict.fromkeys(ALWAYS_COLUMNS, False)
    rows = []
    for item in cache.items:
        types = field_types(item)
        cells: dict[str, Any] = {}
        values = flatten(item, lookups)
        for coordinate, value in values.items():
            secret = is_secret(coordinate, types.get(coordinate))
            ## a field name can be hidden in one item and text in another, any secret cell wins
            secret_columns[coordinate] = secret_columns.get(coordinate, False) or secret
            cells[coordinate.display] = MASKED if secret else value
        row = {
            "id": item.get("id"),
            "revisionDate": item.get("revisionDate"),
            "cells": cells,
            ## what a move or a change of the collections sends back as the values it saw
            ## (operations.shown), the cells hold the names
            "folderId": item.get("folderId") or None,
            "collectionIds": list(item.get("collectionIds") or []),
        }
        if item.get("id") in (own_generator or ()):
            row["ownGenerator"] = True
        empty = empty_secrets(item)
        if empty:
            row["emptySecrets"] = empty
        ## what a rename of an attachment names it by (renameAttachment), never the url (it
        ## holds a token) or the size
        attachments = [
            {"id": attachment.get("id"), "fileName": attachment.get("fileName")}
            for attachment in item.get("attachments") or []
            if isinstance(attachment, dict)
        ]
        if attachments:
            row["attachments"] = attachments
        empty_text = empty_text_fields(item, values)
        if empty_text:
            row["emptyTextFields"] = empty_text
        for key, field_type in (("booleanFields", BOOLEAN_FIELD), ("linkedFields", LINKED_FIELD)):
            listed = fields_of_type(item, field_type)
            if listed:
                row[key] = listed
        rows.append(row)
    for listed in (to_fill or {}).values():
        secret_columns.update(dict.fromkeys(map(parse, listed), True))
    columns = [column(c, secret_columns[c]) for c in sorted(secret_columns, key=sort_key)]
    note_id = tool_note_id(cache, remembered)
    ## the tool note's own field (backup-password) is no field a template should name
    fields = vault_fields(
        [item for item in cache.items if note_id is None or item.get("id") != note_id]
    )
    return {
        "columns": columns,
        "rows": rows,
        "toolNoteId": note_id,
        "fields": fields,
        "organizations": vault_organizations(cache),
        "folders": vault_folders(cache),
    }


@router.get("/grid")
async def grid(request: Request, session: Session) -> dict[str, Any]:
    """build_grid plus toFill, the secret columns to fill in by item id as the vault is now. A
    row has ownGenerator when its item has a generator setting of its own."""
    to_fill = app_state(request).to_fill.by_item()
    own = own_generator_ids(request, session)
    return {**build_grid(session.cache, session.tool_note_id, to_fill, own), "toFill": to_fill}


class ToFillMark(BaseModel):
    itemId: str
    column: str
    mark: bool


@router.post("/to-fill")
async def mark_to_fill(request: Request, body: ToFillMark, session: Session) -> dict[str, Any]:
    """Mark an empty secret cell to fill in, or take a cell off the list. The change is queued
    as a UI entry, the answer has the list as it is with it."""
    to_fill = app_state(request).to_fill
    try:
        entry_id = to_fill.mark(body.itemId, body.column, body.mark)
    except RefusedMark as error:
        raise HTTPException(status_code=422, detail=str(error)) from None
    return {"entryId": entry_id, "toFill": to_fill.by_item()}


@router.get("/near-duplicates")
async def near_duplicates(session: Session) -> list[dict[str, Any]]:
    columns = build_grid(session.cache, session.tool_note_id)["columns"]
    return near_duplicate_groups(column["id"] for column in columns)


@router.get("/items/{item_id}/secret")
async def secret(
    item_id: str, session: Session, column: Annotated[str, Query()], response: Response
) -> dict[str, str]:
    """The one secret value of a cell. no-store keeps the browser from caching it."""
    response.headers["Cache-Control"] = "no-store"
    cache = session.cache
    item = cache.items_by_id().get(item_id)
    try:
        coordinate = parse(column)
    except ValueError:
        coordinate = None
    if item is None or coordinate is None:
        raise HTTPException(status_code=404, detail="No such item or column")
    value = flatten(item, cache.lookups()).get(coordinate)
    if value is None or not is_secret(coordinate, field_types(item).get(coordinate)):
        raise HTTPException(status_code=404, detail="No secret value in this cell")
    return {"value": str(value)}

"""The vault as the agent sees it: every item projected without its secret values, the tool note
left out, and the proposals into the review queue."""

import json
from collections import Counter
from dataclasses import dataclass
from typing import Any

from mcp.server.mcpserver.exceptions import ToolError

from bwtidy.items.coordinates import (
    HIDDEN_FIELD,
    ITEM_TYPES,
    URIS,
    Coordinate,
    Lookups,
    UriMatch,
    Value,
    field_entries,
    field_types,
    flatten,
    is_secret,
    sort_key,
    uri_entries,
    uris_shown,
)
from bwtidy.mcp.texts import DISABLED, TOP_COLUMNS
from bwtidy.operations import Conflict, InvalidOperation, MergeConflicts, Operation
from bwtidy.queue.intake import VaultLocked
from bwtidy.queue.review import ReviewQueue
from bwtidy.queue.view import plural
from bwtidy.sections import InvalidToolNote
from bwtidy.sections.datatypes import Datatype, InvalidDatatypes, stored_datatypes
from bwtidy.sections.templates import (
    FieldCoordinate,
    FieldPatternCoordinate,
    InvalidTemplates,
    Template,
    coordinate_of,
)
from bwtidy.session import SessionManager
from bwtidy.vault import Formats, VaultCache, find_tool_note, stored_templates, template_matches


def dump(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, indent=1)


@dataclass(frozen=True)
class Visible:
    """One item as the agent sees it: its non-secret values by column, the names of the secret
    columns that hold a value and what a proposal names of it, never a secret value."""

    id: str
    name: str
    revision_date: str | None
    ## what a type filter compares with: the type's name, case folded, and its number as text
    type_words: tuple[str, str]
    folder_id: str | None
    collection_ids: list[str]
    values: dict[str, Value]
    secret_columns: list[str]
    ## every column of the item, a custom field also when it is empty
    columns: frozenset[Coordinate]
    uris: list[tuple[str, UriMatch]]
    attachments: list[dict[str, Any]]

    def revision(self) -> str:
        """The revisionDate a proposal carries, which every item bw lists has."""
        if self.revision_date is None:
            raise KeyError("revisionDate")
        return self.revision_date


def visible(item: dict[str, Any], lookups: Lookups) -> Visible:
    """The item as the agent sees it."""
    values, secret = split_values(item, lookups)
    name = ITEM_TYPES.get(item.get("type", 0), "")
    columns = set(flatten(item, lookups)) | {c for c, _ in field_entries(item)}
    return Visible(
        id=item["id"],
        name=item.get("name") or "",
        revision_date=item.get("revisionDate"),
        type_words=(name.casefold(), str(item.get("type"))),
        folder_id=item.get("folderId"),
        collection_ids=list(item.get("collectionIds") or []),
        values=values,
        secret_columns=secret,
        columns=frozenset(columns),
        uris=uri_entries(item),
        attachments=attachment_list(item),
    )


def attachment_list(item: dict[str, Any]) -> list[dict[str, Any]]:
    """Each attachment's id, fileName and listed (encrypted) size in bytes, never its url."""
    listed = []
    for attachment in item.get("attachments") or []:
        if not isinstance(attachment, dict):
            continue
        try:
            size = int(attachment.get("size"))
        except (TypeError, ValueError):
            size = None
        listed.append(
            {"id": attachment.get("id"), "fileName": attachment.get("fileName"), "size": size}
        )
    return listed


def split_values(item: dict[str, Any], lookups: Lookups) -> tuple[dict[str, Value], list[str]]:
    """The non-secret values by column, and the secret columns that hold a value."""
    types = field_types(item)
    values: dict[str, Value] = {}
    secret: list[str] = []
    for coordinate, value in sorted(flatten(item, lookups).items(), key=lambda c: sort_key(c[0])):
        if is_secret(coordinate, types.get(coordinate)):
            secret.append(coordinate.display)
        else:
            values[coordinate.display] = value
    return values, secret


class AgentVault:
    """The vault as the agent sees it: the tool note left out, secret values never included.

    The items it answers are projections (`Visible`), the cache stays behind it.
    """

    def __init__(self, session: SessionManager, queue: ReviewQueue, formats: Formats) -> None:
        self._session = session
        self._queue = queue
        self._formats = formats

    @property
    def _cache(self) -> VaultCache:
        return self._session.cache

    def enabled(self) -> bool:
        return self._session.unlocked and self._session.mcp_enabled and self._cache.loaded

    def _raw_items(self) -> list[dict[str, Any]]:
        note = find_tool_note(self._cache, self._session.tool_note_id)
        return [i for i in self._cache.items if i.get("id") and i is not note]

    def items(self) -> list[Visible]:
        lookups = self._cache.lookups()
        return [visible(i, lookups) for i in self._raw_items()]

    def item(self, item_id: str) -> Visible:
        """The visible item with this id. The tool note is not found, like any unknown id."""
        found = next((i for i in self._raw_items() if i.get("id") == item_id), None)
        if found is None:
            raise ToolError(f"no item with the id {item_id!r}")
        return visible(found, self._cache.lookups())

    def folders(self) -> list[tuple[str, str]]:
        """Each folder's name and id."""
        return [(f.get("name") or "", f["id"]) for f in self._cache.folders if f.get("id")]

    def trashed_per_folder(self) -> Counter[str | None]:
        """How many items in the trash each folder id still holds."""
        return Counter(i.get("folderId") for i in self._cache.trashed)

    def folder(self, name: str) -> str:
        """The id of the one folder with this name, as list_folders names it."""
        found = [f for f in self._cache.folders if f.get("id") and (f.get("name") or "") == name]
        if not found:
            raise ToolError(f"no folder named {name!r}, list_folders names every folder")
        if len(found) > 1:
            raise ToolError(
                f"{len(found)} folders are named {name!r}, the user must rename one first"
            )
        return found[0]["id"]

    def organizations(self) -> dict[str, str]:
        """Each organization's name by its id."""
        return {o["id"]: o.get("name") or "" for o in self._cache.organizations}

    def collections(self) -> list[tuple[str | None, str, str]]:
        """Each collection's organization id, name and id."""
        return [
            (c.get("organizationId"), c.get("name") or "", c["id"])
            for c in self._cache.collections
            if c.get("id")
        ]

    def templates(self) -> tuple[list[Template], str | None]:
        """The stored templates, or none and the problem when the tool note holds no valid ones."""
        try:
            return stored_templates(self._cache, self._session.tool_note_id), None
        except InvalidTemplates as error:
            return [], str(error)

    def _stored_datatypes(self) -> tuple[list[Datatype], str | None]:
        """The datatypes that are not broken, and the problem when the section cannot be read."""
        note = find_tool_note(self._cache, self._session.tool_note_id)
        if note is None:
            return [], None
        try:
            stored = stored_datatypes(note.get("notes"))
        except (InvalidToolNote, InvalidDatatypes) as error:
            return [], str(error)
        return [d for d in stored if d.compiled is not None], None

    def datatypes(self) -> tuple[list[Datatype], str | None]:
        """The datatypes the agent may see, the ones a non-secret template coordinate uses, and
        the problem when the section cannot be read."""
        stored, error = self._stored_datatypes()
        templates, _ = self.templates()
        return _listed_datatypes(stored, templates, self._hidden_field_names()), error

    def datatype_names(self) -> dict[str, str]:
        """The name of each datatype that is not broken, by its id."""
        stored, _ = self._stored_datatypes()
        return {d.id: d.name for d in stored}

    def _hidden_field_names(self) -> set[str]:
        """The names of the custom fields that some item holds as hidden, the tool note too."""
        return {
            entry.get("name") or ""
            for item in self._cache.items
            for entry in item.get("fields") or []
            if entry.get("type") == HIDDEN_FIELD
        }

    def matches(self) -> dict[str, dict[str, Any]]:
        templates, _ = self.templates()
        return template_matches(self._cache, self._formats, templates, for_mcp=True)

    def show(self, item: Visible, matches: dict[str, dict[str, Any]]) -> dict[str, Any]:
        values: dict[str, Any] = dict(item.values)
        shown: dict[str, Any] = {"id": item.id}
        for column in TOP_COLUMNS:
            shown[column] = values.pop(column, None)
        values.pop("attachments", None)
        ## the agent sees each URI's match setting, the grid and the search only the strings
        if URIS in values:
            values[URIS] = uris_shown(item.uris)
        shown["values"] = values
        shown["attachments"] = item.attachments
        shown["secretColumns"] = item.secret_columns
        shown["template"] = matches.get(item.id, {"match": None, "scores": []})
        return shown

    def proposals(self, status: str | None) -> list[dict[str, Any]]:
        return self._queue.proposals(status)

    def propose(self, op: Operation, summary: str, reason: str | None) -> dict[str, Any]:
        if reason and reason.strip():
            summary = f"{summary}. Reason: {reason.strip()}"
        try:
            entry_id = self._queue.enqueue(op, summary, source="mcp")
        except MergeConflicts as error:
            conflicts = dump(error.conflicts)
            raise ToolError(
                "These items hold values in more than one of the merged columns. Choose the "
                f"column to keep per item and pass it in resolutions: {conflicts}"
            ) from None
        except Conflict as error:
            if all("folderId" in conflict for conflict in error.conflicts):
                raise ToolError(
                    "the folder changed in the vault, list the folders again and propose again"
                ) from None
            raise ToolError(
                f"{plural(len(error.conflicts), 'item', 'items')} changed in the vault, "
                "list the items again and propose again"
            ) from None
        except InvalidOperation as error:
            raise ToolError(f"refused: {error.reason}") from None
        except VaultLocked:
            raise ToolError(DISABLED) from None
        return {"entryId": entry_id, "summary": summary}


def _listed_datatypes(
    datatypes: list[Datatype], templates: list[Template], hidden_names: set[str]
) -> list[Datatype]:
    """The datatypes that at least one non-secret template coordinate uses.

    A field is secret when the template or any item holds it hidden, the safer of the two.
    Reason: an example typed as a real value of a secret column would reach the agent.
    """
    plain: set[str] = set()
    for template in templates:
        for coordinate in template.coordinates:
            if coordinate.datatype is None:
                continue
            if isinstance(coordinate, FieldCoordinate):
                secret = coordinate.fieldType == HIDDEN_FIELD or coordinate.name in hidden_names
            elif isinstance(coordinate, FieldPatternCoordinate):
                secret = coordinate.fieldType == HIDDEN_FIELD or any(
                    coordinate.matches(name) for name in hidden_names
                )
            else:
                secret = is_secret(coordinate_of(coordinate))
            if not secret:
                plain.add(coordinate.datatype)
    return [d for d in datatypes if d.id in plain]

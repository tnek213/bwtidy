"""The read tools: the vault without its secret values, the tool note's sections the agent may
see, and the agent's own proposals. Each answers from the agent's view (`Visible`) only."""

from collections import Counter
from typing import Annotated, Any, Literal

from pydantic import Field

from bwtidy.items.coordinates import Value
from bwtidy.items.normalize import near_duplicate_groups
from bwtidy.mcp.texts import ItemId
from bwtidy.mcp.vault import AgentVault, Visible, dump
from bwtidy.sections.datatypes import Datatype
from bwtidy.sections.templates import Template, column_of, template_list


def _contains(values: dict[str, Value], needle: str) -> bool:
    for value in values.values():
        parts = value if isinstance(value, list) else [value]
        if any(needle in str(part).casefold() for part in parts):
            return True
    return False


def _type_matches(item: Visible, wanted: str) -> bool:
    return wanted.casefold() in item.type_words


async def list_items(
    vault: AgentVault,
    text: Annotated[
        str | None,
        Field(description="Case-insensitive text in the name, a file name or any non-secret value"),
    ] = None,
    type: Annotated[str | None, Field(description="login, note, card, identity or sshKey")] = None,
    folder: Annotated[
        str | None, Field(description="Folder name, empty for items without a folder")
    ] = None,
    organization: Annotated[
        str | None,
        Field(description="Organization name, empty for items in the personal vault"),
    ] = None,
    collection: Annotated[str | None, Field(description="Collection name")] = None,
    template: Annotated[
        str | None,
        Field(description="Template name the item matches, empty for items matching none"),
    ] = None,
) -> str:
    matches = vault.matches()
    needle = text.casefold() if text else None
    found = []
    for item in vault.items():
        values = item.values
        if needle is not None and not _contains(values, needle):
            continue
        if type is not None and not _type_matches(item, type):
            continue
        if folder is not None and values.get("folder", "") != folder:
            continue
        if organization is not None and values.get("organization", "") != organization:
            continue
        collections = values.get("collections")
        in_collection = isinstance(collections, list) and collection in collections
        if collection is not None and not in_collection:
            continue
        match = matches.get(item.id, {}).get("match")
        if template is not None and (match or "") != template:
            continue
        found.append(vault.show(item, matches))
    return dump({"count": len(found), "items": found})


async def get_item(vault: AgentVault, item_id: ItemId) -> str:
    item = vault.item(item_id)
    return dump(vault.show(item, vault.matches()))


async def list_folders(vault: AgentVault) -> str:
    counts = Counter(i.folder_id for i in vault.items())
    trashed = vault.trashed_per_folder()
    named = sorted(vault.folders(), key=lambda f: f[0].casefold())
    folders = [
        {
            "name": name,
            "items": counts.get(folder_id, 0),
            "itemsInTrash": trashed.get(folder_id, 0),
        }
        for name, folder_id in named
    ]
    return dump({"folders": folders, "itemsWithoutFolder": counts.get(None, 0)})


async def list_collections(vault: AgentVault) -> str:
    counts = Counter(c for i in vault.items() for c in i.collection_ids)
    organizations = vault.organizations()
    named = sorted(
        (
            (organizations.get(organization_id, ""), name, collection_id)
            for organization_id, name, collection_id in vault.collections()
        ),
        key=lambda c: (c[0].casefold(), c[1].casefold()),
    )
    collections = [
        {"name": name, "organization": organization, "items": counts.get(collection_id, 0)}
        for organization, name, collection_id in named
    ]
    return dump({"organizations": sorted(organizations.values()), "collections": collections})


def _with_datatype_names(templates: list[Template], names: dict[str, str]) -> list[dict[str, Any]]:
    """The templates with each datatype by name. An unknown or broken one counts as none."""
    listed = template_list(templates)
    for template in listed:
        for coordinate in template["coordinates"]:
            datatype = names.get(coordinate.pop("datatype", None))
            if datatype is not None:
                coordinate["datatype"] = datatype
    return listed


async def list_templates(vault: AgentVault) -> str:
    templates, error = vault.templates()
    answer: dict[str, Any] = {"templates": _with_datatype_names(templates, vault.datatype_names())}
    if error is not None:
        answer["error"] = error
    return dump(answer)


def _datatype_json(datatype: Datatype, templates: list[Template]) -> dict[str, Any]:
    answer: dict[str, Any] = {
        "name": datatype.name,
        "pattern": datatype.pattern,
        "description": datatype.description,
        "ignoreCase": datatype.ignoreCase,
    }
    if not datatype.wholeValue:
        answer["wholeValue"] = False
    return answer | {
        "examples": list(datatype.examples),
        "counterExamples": list(datatype.counterExamples),
        "usedBy": [
            {"template": template.name, "column": column_of(coordinate)}
            for template in templates
            for coordinate in template.coordinates
            if coordinate.datatype == datatype.id
        ],
    }


async def list_datatypes(vault: AgentVault) -> str:
    templates, templates_error = vault.templates()
    listed, datatypes_error = vault.datatypes()
    answer: dict[str, Any] = {"datatypes": [_datatype_json(d, templates) for d in listed]}
    error = datatypes_error or templates_error
    if error is not None:
        answer["error"] = error
    return dump(answer)


async def list_near_duplicates(vault: AgentVault) -> str:
    columns = {c for i in vault.items() for c in (*i.values, *i.secret_columns)}
    return dump(near_duplicate_groups(sorted(columns)))


async def list_proposals(
    vault: AgentVault,
    status: Annotated[
        Literal["pending", "approved", "rejected", "applied", "failed"] | None,
        Field(description="Only the proposals with this status"),
    ] = None,
) -> str:
    return dump({"proposals": vault.proposals(status)})

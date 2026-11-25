"""The changes PUT /api/templates and PUT /api/datatypes take, for the tests of every suite.

template_changes and datatype_changes turn a whole list into the changes that make it of
another, for a test that puts a stored list back as it found it.
"""

from typing import Any

## what GET /api/datatypes lists besides what is stored
DATATYPE_FACTS = ("states", "maxRepeat", "shortest", "broken")


def add_template(template: dict[str, Any]) -> dict[str, Any]:
    return {"op": "addTemplate", "template": template}


def set_template(template: dict[str, Any]) -> dict[str, Any]:
    return {"op": "setTemplate", "template": template}


def rename_template(name: str, to: str) -> dict[str, Any]:
    return {"op": "renameTemplate", "name": name, "to": to}


def move_template(name: str, index: int) -> dict[str, Any]:
    return {"op": "moveTemplate", "name": name, "index": index}


def remove_template(name: str) -> dict[str, Any]:
    return {"op": "removeTemplate", "name": name}


def stored_datatype(datatype: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in datatype.items() if key not in DATATYPE_FACTS}


def add_datatype(datatype: dict[str, Any]) -> dict[str, Any]:
    """A new datatype, its id left out (the route makes it)."""
    new = {key: value for key, value in stored_datatype(datatype).items() if key != "id"}
    return {"op": "addDatatype", "datatype": new}


def set_datatype(datatype: dict[str, Any]) -> dict[str, Any]:
    return {"op": "setDatatype", "datatype": stored_datatype(datatype)}


def remove_datatype(datatype_id: str) -> dict[str, Any]:
    return {"op": "removeDatatype", "id": datatype_id}


def changes(*listed: dict[str, Any]) -> dict[str, Any]:
    """The body of a PUT."""
    return {"changes": list(listed)}


def template_changes(before: list[dict[str, Any]], after: list[dict[str, Any]]) -> list[Any]:
    """The changes that turn the templates before into after, by name."""
    names = {template["name"] for template in after}
    old = {template["name"] for template in before}
    found = [remove_template(t["name"]) for t in before if t["name"] not in names]
    for template in after:
        found.append(set_template(template) if template["name"] in old else add_template(template))
    ## the changes leave the kept ones in their order, the new ones after them
    left = [t["name"] for t in before if t["name"] in names]
    left += [t["name"] for t in after if t["name"] not in old]
    if left != [t["name"] for t in after]:
        found += [move_template(t["name"], index) for index, t in enumerate(after)]
    return found


def datatype_changes(before: list[dict[str, Any]], after: list[dict[str, Any]]) -> list[Any]:
    """The changes that turn the datatypes before into after, by id. One of after without an
    id, or with one before does not hold, is new."""
    ids = {datatype.get("id") for datatype in after}
    old = {datatype["id"] for datatype in before}
    found = [remove_datatype(d["id"]) for d in before if d["id"] not in ids]
    for datatype in after:
        new = datatype.get("id") not in old
        found.append(add_datatype(datatype) if new else set_datatype(datatype))
    return found

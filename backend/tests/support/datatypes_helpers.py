"""Datatypes, templates, coordinates and items the tests of the templates and the datatypes
share."""

import copy
from typing import Any

from bwtidy.sections.templates import parse_templates
from tests.support.app_env import AppEnv
from tests.support.sections_helpers import write_notes

TEXT, HIDDEN, BOOLEAN = 0, 1, 2
NOTE, LOGIN = 2, 1
PIN_ID, DATE_ID, URL_ID = "0000aaaa", "0000bbbb", "0000cccc"
PIN = {"id": PIN_ID, "name": "PIN", "pattern": r"\d{4,8}"}
DATE = {"id": DATE_ID, "name": "ISO date", "pattern": r"\d{4}-\d{2}-\d{2}"}
URL = {"id": URL_ID, "name": "Web address", "pattern": r"https://\S+"}
## a value PIN takes and one a single edit away from it
CONFORMING = "1234"
OFF_FORMAT = " 1234"


def field(name: str, field_type: int = TEXT, datatype: str | None = None) -> dict[str, Any]:
    coordinate = {"kind": "field", "name": name, "fieldType": field_type}
    return {**coordinate, "datatype": datatype} if datatype else coordinate


def path(value: str, datatype: str | None = None) -> dict[str, Any]:
    coordinate = {"kind": "path", "path": value}
    return {**coordinate, "datatype": datatype} if datatype else coordinate


def template(*coordinates: dict[str, Any], name: str = "T", type_: int = NOTE) -> dict[str, Any]:
    return {"name": name, "type": type_, "coordinates": list(coordinates)}


def stored(entry: dict[str, Any]) -> dict[str, Any]:
    """The entry with every key of the section."""
    return {
        "description": "",
        "ignoreCase": False,
        "examples": [],
        "counterExamples": [],
        **entry,
    }


def one(*coordinates: dict[str, Any], type_: int = NOTE) -> Any:
    return parse_templates([template(*coordinates, type_=type_)])[0]


def item(
    *fields: tuple[str, str | None, int], item_id: str = "dt-1", type_: int = NOTE, **extra: Any
) -> dict[str, Any]:
    return {
        "object": "item",
        "id": item_id,
        "folderId": None,
        "type": type_,
        "reprompt": 0,
        "name": f"Item {item_id}",
        "favorite": False,
        "fields": [{"name": n, "value": v, "type": t, "linkedId": None} for n, v, t in fields],
        "revisionDate": "2026-10-30T09:00:00.000Z",
        **extra,
    }


def a_login(*uris: str, item_id: str = "dt-uris") -> dict[str, Any]:
    login = {"uris": [{"match": None, "uri": u} for u in uris], "username": "dev"}
    return item(item_id=item_id, type_=LOGIN, login=login)


def store(env: AppEnv, datatypes: Any, templates: list[Any], *items: dict[str, Any]) -> None:
    """Datatypes, templates and items as another Bitwarden client would have left them."""
    env.bw.items.extend(copy.deepcopy(list(items)))
    write_notes(env, {"version": 1, "templates": templates, "datatypes": datatypes})
    env.reload()


def scores_of(env: AppEnv, item_id: str) -> list[dict[str, Any]]:
    response = env.client.get("/api/template-matches")
    assert response.status_code == 200
    return response.json()[item_id]["scores"]


COMPUTER = {
    "name": "Computer",
    "type": 2,
    "coordinates": [
        field("user"),
        field("user password", HIDDEN),
        field("BIOS password", HIDDEN),
        field("disk encryption", HIDDEN),
    ],
}


NOTE_WITH_USER = {"name": "Note with user", "type": 2, "coordinates": [field("user")]}


WEB_LOGIN = {
    "name": "Web login",
    "type": 1,
    "coordinates": [path("login.username"), path("login.password"), path("login.uris")],
}


## a computer as a login, like ThinkPad X1 in the dev vault
THINKPAD: dict[str, Any] = {
    "object": "item",
    "id": "login-2",
    "folderId": "f1",
    "type": 1,
    "reprompt": 0,
    "name": "ThinkPad",
    "favorite": False,
    "fields": [
        {"name": "User", "value": "dev", "type": 0, "linkedId": None},
        {"name": "BIOS Password", "value": "bios-secret-31", "type": 1, "linkedId": None},
        {"name": "LUKS", "value": "luks-secret-32", "type": 1, "linkedId": None},
        {"name": "Windows key", "value": "AAAAA-BBBBB", "type": 0, "linkedId": None},
    ],
    "login": {"uris": [], "username": None, "password": "user-secret-33", "totp": None},
    "attachments": [{"id": "a9", "fileName": "recovery.txt", "size": "60"}],
    "revisionDate": "2026-10-26T09:30:00.000Z",
}


## a computer as a note, like Media PC in the dev vault
MEDIA_PC: dict[str, Any] = {
    "object": "item",
    "id": "note-3",
    "type": 2,
    "reprompt": 0,
    "name": "Media PC",
    "notes": "Under the TV",
    "favorite": False,
    "fields": [
        {"name": "user", "value": "media", "type": 0, "linkedId": None},
        {"name": "user password", "value": "media-secret-41", "type": 1, "linkedId": None},
        {"name": "dm-crypt password", "value": "disk-secret-42", "type": 1, "linkedId": None},
        {"name": "Serial", "value": "SN-9", "type": 0, "linkedId": None},
    ],
    "secureNote": {"type": 0},
    "revisionDate": "2026-10-26T09:40:00.000Z",
}


def note(*fields: tuple[str, str]) -> dict[str, Any]:
    """A note with text fields."""
    values = [{"name": n, "value": v, "type": 0, "linkedId": None} for n, v in fields]
    return {**copy.deepcopy(MEDIA_PC), "fields": values}

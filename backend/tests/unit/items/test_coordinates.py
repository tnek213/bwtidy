"""The coordinates of items/coordinates.py: an item flattened into columns, a coordinate parsed and
shown, and set_leaf and remove_leaf on raw items, which change the touched leaf and nothing else."""

import copy
import json
from typing import Any

import pytest

from bwtidy.items.coordinates import (
    Coordinate,
    Lookups,
    field_types,
    flatten,
    is_secret,
    parse,
    remove_leaf,
    set_leaf,
    set_uris,
    sort_key,
    uri_entries,
    uris_shown,
)
from tests.support.quiet import assert_absent
from tests.support.sample_vault import (
    CARD,
    COLLECTIONS,
    FOLDERS,
    IDENTITY,
    LOGIN,
    NOTE,
    ORGANIZATIONS,
    SSH_KEY,
)

LOOKUPS = Lookups.build(FOLDERS, ORGANIZATIONS, COLLECTIONS)


def flat(item: dict) -> dict[str, Any]:
    return {c.display: v for c, v in flatten(item, LOOKUPS).items()}


def test_flatten_a_login():
    assert flat(LOGIN) == {
        "name": "Example Mail",
        "type": "login",
        "folder": "Work",
        "favorite": True,
        "attachments": ["codes.txt"],
        "passkeys": 1,
        "revisionDate": "2026-10-26T10:00:00.000Z",
        "login.username": "dev@mail.example.test",
        "login.password": "pw-login-secret-e0e7",
        "login.totp": "otpauth://totp/Mail:dev?secret=TOTPSECRETKRSXG5",
        "login.uris": ["https://mail.example.test", "https://webmail.example.test"],
        "fields.User": "dev",
        "fields.PIN": "pin-secret-4711",
    }


@pytest.mark.parametrize(
    ("attachments", "names"),
    [
        (None, None),
        ([], None),
        ([{"id": "a1", "fileName": "one.txt", "size": "5", "url": "https://x/t"}], ["one.txt"]),
        (
            [
                {"id": "a2", "fileName": "b.pdf", "size": "9", "url": "https://x/u"},
                {"id": "a1", "fileName": "a.txt", "size": "5", "url": "https://x/t"},
            ],
            ["b.pdf", "a.txt"],
        ),
    ],
)
def test_the_attachments_column_lists_the_file_names_in_the_items_order(attachments, names):
    item = {"id": "x", "type": 2, "attachments": attachments}
    assert flat(item).get("attachments") == names
    assert "https://x/t" not in str(flat(item))


def test_flatten_a_note_with_org_collections_and_odd_field_names():
    assert flat(NOTE) == {
        "name": "Backup disk",
        "type": "note",
        "organization": "Family",
        "collections": ["shared", "Youth"],
        "notes": "Free text, not secret",
        "reprompt": True,
        "revisionDate": "2026-10-26T11:00:00.000Z",
        "fields.Encryption passphrase ": "luks-secret-9f8e",
        "fields.dm.crypt.v2": "dotted text",
        "fields.WPA key": "wpa-main-secret",
        "fields.WPA key#2": "wpa-guest-secret",
        "fields.Empty#2": "second empty has a value",
        "fields.Enabled": "false",
    }


def test_field_names_are_kept_verbatim():
    values = flatten(NOTE, LOOKUPS)
    assert values[Coordinate.field("Encryption passphrase ")] == "luks-secret-9f8e"
    assert values[Coordinate.field("dm.crypt.v2")] == "dotted text"
    assert values[Coordinate.field("WPA key", 2)] == "wpa-guest-secret"
    assert Coordinate.field("Empty") not in values


def test_flatten_a_card():
    assert flat(CARD) == {
        "name": "Visa debit",
        "type": "card",
        "revisionDate": "2026-10-26T12:00:00.000Z",
        "card.cardholderName": "Dev User",
        "card.brand": "Visa",
        "card.number": "4111111111111111",
        "card.expMonth": "12",
        "card.expYear": "2029",
        "card.code": "9871",
    }


def test_flatten_an_identity_leaves_out_empty_values():
    assert flat(IDENTITY) == {
        "name": "Me",
        "type": "identity",
        "revisionDate": "2026-10-26T13:00:00.000Z",
        "identity.title": "Mr",
        "identity.firstName": "Dev",
        "identity.lastName": "User",
        "identity.email": "dev@example.test",
        "identity.ssn": "19700101-0000",
    }


def test_flatten_an_ssh_key():
    values = flat(SSH_KEY)
    assert values["type"] == "sshKey"
    assert values["sshKey.privateKey"].startswith("-----BEGIN OPENSSH")
    assert values["sshKey.publicKey"] == "ssh-ed25519 AAAAC3Nza dev@host"
    assert values["sshKey.keyFingerprint"] == "SHA256:abcdef"


def test_never_leaf_keys_are_absent():
    keys = set(flat(LOGIN)) | set(flat(NOTE))
    for never in (
        "id",
        "object",
        "creationDate",
        "deletedDate",
        "passwordHistory",
        "login.passwordRevisionDate",
        "login.fido2Credentials",
        "secureNote.type",
        "fields.Linked",
    ):
        assert never not in keys
    assert not any(k.startswith(("attachments.", "passwordHistory", "secureNote")) for k in keys)


def test_unknown_folder_falls_back_to_the_id():
    item = {"id": "x", "type": 1, "folderId": "gone", "collectionIds": ["c9"]}
    assert flat(item) == {"type": "login", "folder": "gone", "collections": ["c9"]}


def test_hidden_fields_and_secret_paths_are_secret():
    types = field_types(NOTE)
    assert is_secret(Coordinate.field("WPA key", 2), types[Coordinate.field("WPA key", 2)])
    assert not is_secret(Coordinate.field("dm.crypt.v2"), types[Coordinate.field("dm.crypt.v2")])
    assert not is_secret(Coordinate.field("Enabled"), types[Coordinate.field("Enabled")])
    for path in ("login.password", "login.totp", "card.number", "card.code", "sshKey.privateKey"):
        assert is_secret(Coordinate.at(path))
    for path in ("name", "notes", "login.uris", "login.username", "sshKey.publicKey"):
        assert not is_secret(Coordinate.at(path))


def test_groups():
    assert Coordinate.at("name").group == "item"
    assert Coordinate.at("collections").group == "item"
    assert Coordinate.at("login.uris").group == "login"
    assert Coordinate.at("identity.ssn").group == "identity"
    assert Coordinate.at("sshKey.publicKey").group == "sshKey"
    assert Coordinate.field("login.username").group == "fields"


@pytest.mark.parametrize(
    "coordinate",
    [
        Coordinate.at("name"),
        Coordinate.at("login.uris"),
        Coordinate.at("identity.passportNumber"),
        Coordinate.field("BIOS Password"),
        Coordinate.field("Encryption passphrase "),
        Coordinate.field("a.b.c"),
        Coordinate.field("login.password"),
        Coordinate.field("user", 2),
        Coordinate.field("user", 11),
        Coordinate.field("key#2"),
        Coordinate.field("key#2", 3),
        Coordinate.field("#1"),
        Coordinate.field(""),
        Coordinate.field("trailing#"),
        Coordinate.field("hash#0"),
    ],
)
def test_display_round_trip(coordinate: Coordinate):
    assert parse(coordinate.display) == coordinate


def test_display_strings():
    assert Coordinate.field("user", 2).display == "fields.user#2"
    assert Coordinate.field("BIOS Password").display == "fields.BIOS Password"
    assert parse("fields.login.password") == Coordinate.field("login.password")
    assert parse("fields.x#2") == Coordinate.field("x", 2)


@pytest.mark.parametrize("display", ["", "login", "login.nope", "fields", "id", "passwordHistory"])
def test_parse_rejects_unknown_coordinates(display: str):
    with pytest.raises(ValueError):
        parse(display)


def test_sort_order():
    shuffled = [
        Coordinate.field("b"),
        Coordinate.at("login.username"),
        Coordinate.field("A", 2),
        Coordinate.at("revisionDate"),
        Coordinate.at("card.number"),
        Coordinate.field("A"),
        Coordinate.at("name"),
        Coordinate.at("sshKey.privateKey"),
        Coordinate.at("identity.title"),
        Coordinate.at("folder"),
    ]
    assert [c.display for c in sorted(shuffled, key=sort_key)] == [
        "name",
        "folder",
        "revisionDate",
        "login.username",
        "card.number",
        "identity.title",
        "sshKey.privateKey",
        "fields.A",
        "fields.A#2",
        "fields.b",
    ]


def dumps(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True)


def changed(item: dict[str, Any], column: str, value: Any = None, field_type: int | None = None):
    """A copy of item after set_leaf (or remove_leaf when value is None)."""
    copied = copy.deepcopy(item)
    if value is None:
        remove_leaf(copied, parse(column))
    else:
        set_leaf(copied, parse(column), value, field_type)
    return copied


def assert_others_untouched(before: dict[str, Any], after: dict[str, Any], *touched: str) -> None:
    """Every top-level key except the touched ones is byte for byte the same."""
    for key in set(before) | set(after):
        if key in touched:
            continue
        assert dumps(before.get(key)) == dumps(after.get(key)), key


def test_set_item_leaves():
    after = changed(LOGIN, "name", "Renamed")
    assert after["name"] == "Renamed"
    assert_others_untouched(LOGIN, after, "name")
    assert changed(LOGIN, "notes", "some text")["notes"] == "some text"
    assert changed(NOTE, "favorite", True)["favorite"] is True
    assert changed(LOGIN, "favorite", "false")["favorite"] is False
    assert changed(LOGIN, "reprompt", True)["reprompt"] == 1
    assert changed(NOTE, "reprompt", "false")["reprompt"] == 0


@pytest.mark.parametrize("item", [LOGIN, NOTE], ids=["reprompt-off", "reprompt-on"])
def test_reprompt_on_and_off(item: dict[str, Any]):
    on = changed(item, "reprompt", True)
    assert on["reprompt"] == 1
    assert_others_untouched(item, on, "reprompt")
    off = changed(on, "reprompt", False)
    assert off["reprompt"] == 0
    assert_others_untouched(item, off, "reprompt")
    assert changed(off, "reprompt", "true")["reprompt"] == 1
    assert changed(on, "reprompt", "false")["reprompt"] == 0


def test_set_login_leaf_keeps_passkeys_history_and_attachments_byte_for_byte():
    after = changed(LOGIN, "login.password", "new-pw-value")
    assert after["login"]["password"] == "new-pw-value"
    for key in ("uris", "username", "totp", "passwordRevisionDate", "fido2Credentials"):
        assert dumps(after["login"][key]) == dumps(LOGIN["login"][key])
    assert_others_untouched(LOGIN, after, "login")
    assert dumps(after["passwordHistory"]) == dumps(LOGIN["passwordHistory"])
    assert dumps(after["attachments"]) == dumps(LOGIN["attachments"])
    assert after["id"] == "login-1"
    assert after["deletedDate"] is None


def test_set_uris_replaces_the_list_with_its_matches_and_nothing_else():
    after = copy.deepcopy(LOGIN)
    set_uris(after, [("https://b.example.test", 4), ("https://webmail.example.test", None)])
    assert after["login"]["uris"] == [
        {"match": 4, "uri": "https://b.example.test"},
        {"match": None, "uri": "https://webmail.example.test"},
    ]
    assert_others_untouched(LOGIN, after, "login")
    for key in ("username", "password", "totp", "passwordRevisionDate", "fido2Credentials"):
        assert dumps(after["login"][key]) == dumps(LOGIN["login"][key])
    assert uris_shown(uri_entries(after)) == [
        {"uri": "https://b.example.test", "match": "regex"},
        {"uri": "https://webmail.example.test", "match": None},
    ]
    with pytest.raises(ValueError, match="only a login has URIs"):
        set_uris(copy.deepcopy(NOTE), [])


def test_uri_entries_leave_out_an_entry_without_a_uri():
    item = {"type": 1, "login": {"uris": [{"uri": None, "match": 1}, {"uri": "a", "match": 2}]}}
    assert uri_entries(item) == [("a", 2)]
    assert uri_entries(NOTE) == []


def test_set_uris_keeps_the_match_of_a_kept_uri():
    after = changed(
        LOGIN, "login.uris", ["https://webmail.example.test", "https://new.example.test"]
    )
    assert after["login"]["uris"] == [
        {"match": 3, "uri": "https://webmail.example.test"},
        {"match": None, "uri": "https://new.example.test"},
    ]
    assert after["login"]["username"] == LOGIN["login"]["username"]


def test_set_card_identity_and_ssh_leaves():
    assert changed(CARD, "card.code", "111")["card"]["code"] == "111"
    after = changed(IDENTITY, "identity.company", "ACME")
    assert after["identity"]["company"] == "ACME"
    assert after["identity"]["firstName"] == "Dev"
    assert changed(SSH_KEY, "sshKey.publicKey", "ssh-ed25519 new")["sshKey"]["publicKey"] == (
        "ssh-ed25519 new"
    )


def test_set_existing_field_changes_only_its_value():
    after = changed(LOGIN, "fields.User", "other", field_type=1)
    assert after["fields"][0] == {"name": "User", "value": "other", "type": 0, "linkedId": None}
    assert dumps(after["fields"][1:]) == dumps(LOGIN["fields"][1:])
    assert_others_untouched(LOGIN, after, "fields")


def test_new_field_gets_the_given_type_and_text_by_default():
    hidden = changed(LOGIN, "fields.Recovery", "codes", field_type=1)
    assert hidden["fields"][-1] == {
        "name": "Recovery",
        "value": "codes",
        "type": 1,
        "linkedId": None,
    }
    assert dumps(hidden["fields"][:-1]) == dumps(LOGIN["fields"])
    text = changed(LOGIN, "fields.Recovery", "codes")
    assert text["fields"][-1]["type"] == 0
    boolean = changed(LOGIN, "fields.Flag", True, field_type=2)
    assert boolean["fields"][-1] == {"name": "Flag", "value": "true", "type": 2, "linkedId": None}


def test_new_field_on_an_item_without_fields():
    after = changed(IDENTITY, "fields.Note", "x")
    assert after["fields"] == [{"name": "Note", "value": "x", "type": 0, "linkedId": None}]
    assert_others_untouched(IDENTITY, after, "fields")


def test_boolean_field_takes_true_or_false():
    assert changed(NOTE, "fields.Enabled", True)["fields"][6]["value"] == "true"
    assert changed(NOTE, "fields.Enabled", "false")["fields"][6]["value"] == "false"
    with pytest.raises(ValueError):
        changed(NOTE, "fields.Enabled", "maybe")


def test_second_occurrence_is_addressed_exactly():
    after = changed(NOTE, "fields.WPA key#2", "guest-new")
    assert after["fields"][2]["value"] == "wpa-main-secret"
    assert after["fields"][3]["value"] == "guest-new"
    first = changed(NOTE, "fields.Empty", "now set")
    assert first["fields"][4]["value"] == "now set"
    assert first["fields"][5]["value"] == "second empty has a value"
    third = changed(NOTE, "fields.WPA key#3", "third", field_type=1)
    assert third["fields"][-1] == {"name": "WPA key", "value": "third", "type": 1, "linkedId": None}
    with pytest.raises(ValueError) as gap:
        changed(NOTE, "fields.WPA key#5", "gap")
    assert str(gap.value) == "fill fields.WPA key#3 first, the item has only 2 WPA key fields"


def test_a_later_occurrence_names_the_one_to_fill_first():
    with pytest.raises(ValueError) as error:
        changed(LOGIN, "fields.WPA key#2", "pw-like-value")
    assert str(error.value) == "fill fields.WPA key first, the item has no WPA key field yet"
    with pytest.raises(ValueError) as error:
        changed(NOTE, "fields.Enabled#3", "true")
    assert str(error.value) == "fill fields.Enabled#2 first, the item has only 1 Enabled field"


def test_linked_field_cannot_take_a_value():
    with pytest.raises(ValueError, match="linked"):
        changed(LOGIN, "fields.Linked", "x")


def test_leaf_of_another_type_is_refused():
    with pytest.raises(ValueError, match="a note has no login.username"):
        changed(NOTE, "login.username", "x")
    with pytest.raises(ValueError, match="a login has no card.code"):
        changed(LOGIN, "card.code", "1")
    with pytest.raises(ValueError, match="a card has no sshKey.privateKey"):
        changed(CARD, "sshKey.privateKey")


@pytest.mark.parametrize(
    "column",
    ["type", "folder", "organization", "collections", "attachments", "passkeys", "revisionDate"],
)
def test_non_leaves_are_refused_for_set_and_remove(column: str):
    with pytest.raises(ValueError, match="not a leaf"):
        changed(LOGIN, column, "x")
    with pytest.raises(ValueError, match="not a leaf"):
        changed(LOGIN, column)


def test_wrong_value_shapes_are_refused_without_echoing_the_value():
    for column, value in [
        ("login.uris", "pw-like-value-1"),
        ("name", ["pw-like-value-2"]),
        ("login.password", 7),
        ("favorite", "pw-like-value-3"),
        ("fields.User", ["pw-like-value-4"]),
    ]:
        with pytest.raises(ValueError) as error:
            changed(LOGIN, column, value)
        assert_absent("pw-like-value", str(error.value), "the error message")
    with pytest.raises(ValueError, match="empty"):
        changed(LOGIN, "name", "")


def test_remove_field_drops_exactly_that_entry():
    after = changed(NOTE, "fields.WPA key#2")
    assert [f["value"] for f in after["fields"]] == [
        "luks-secret-9f8e",
        "dotted text",
        "wpa-main-secret",
        "",
        "second empty has a value",
        "false",
    ]
    assert_others_untouched(NOTE, after, "fields")
    first = changed(NOTE, "fields.WPA key")
    values_match = [f["value"] for f in first["fields"] if f["name"] == "WPA key"] == [
        "wpa-guest-secret"
    ]
    assert values_match, "the WPA key field does not hold the new value"
    with pytest.raises(ValueError, match="does not exist"):
        changed(NOTE, "fields.Nope")


def test_remove_built_in_leaves():
    assert changed(NOTE, "notes")["notes"] is None
    assert changed(LOGIN, "favorite")["favorite"] is False
    assert changed(NOTE, "reprompt")["reprompt"] == 0
    after = changed(LOGIN, "login.totp")
    assert after["login"]["totp"] is None
    assert after["login"]["password"] == LOGIN["login"]["password"]
    assert dumps(after["login"]["fido2Credentials"]) == dumps(LOGIN["login"]["fido2Credentials"])
    assert changed(LOGIN, "login.uris")["login"]["uris"] == []
    assert changed(CARD, "card.code")["card"]["code"] is None
    with pytest.raises(ValueError, match="required"):
        changed(LOGIN, "name")


def test_set_leaf_takes_a_structured_coordinate():
    item = copy.deepcopy(NOTE)
    set_leaf(item, Coordinate.field("dm.crypt.v2"), "changed")
    assert item["fields"][1]["value"] == "changed"
    remove_leaf(item, Coordinate.field("Encryption passphrase "))
    assert item["fields"][0]["name"] == "dm.crypt.v2"

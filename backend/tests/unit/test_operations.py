"""columns: the coordinates a change log row names for each kind of operation."""

from typing import Any

import pytest

from bwtidy.operations import Operation, columns, parse_operations

REVISION = "2026-11-12T10:00:00Z"


def one(**fields: Any) -> Operation:
    [op] = parse_operations([fields])
    return op


@pytest.mark.parametrize("kind", ["setLeaf", "removeLeaf", "generateLeaf"])
def test_a_leaf_operation_names_its_column(kind: str):
    extra = {"value": "x"} if kind == "setLeaf" else {}
    op = one(kind=kind, itemId="a", revisionDate=REVISION, column="fields.Pin", **extra)
    assert columns(op) == ["fields.Pin"]


def test_a_rename_of_an_item_is_a_set_leaf_of_its_name():
    op = one(kind="setLeaf", itemId="a", revisionDate=REVISION, column="name", value="New")
    assert columns(op) == ["name"]


def test_folder_collections_and_organization_moves_name_what_they_change():
    folder = one(
        kind="moveToFolder", itemIds=["a", "b"], revisionDates={"a": REVISION}, folderName="Work"
    )
    collections = one(
        kind="setCollections", itemId="a", revisionDate=REVISION, collectionNames=["Family"]
    )
    to_organization = one(
        kind="moveToOrganization",
        itemId="a",
        revisionDate=REVISION,
        organizationName="Home",
        collectionNames=["Family"],
    )
    to_personal = one(kind="moveToPersonal", itemId="a", revisionDate=REVISION)
    assert columns(folder) == ["folder"]
    assert columns(collections) == ["collections"]
    assert columns(to_organization) == ["organization", "collections"]
    assert columns(to_personal) == ["organization", "collections"]


def test_a_mapping_names_each_moved_source_and_its_target():
    mapping = {"login.username": "fields.username", "notes": "notes", "fields.Pin": "fields.PIN"}
    convert = one(kind="convertItem", itemId="a", revisionDate=REVISION, newType=1, mapping=mapping)
    template = one(
        kind="applyTemplate", itemId="a", revisionDate=REVISION, template="T", mapping=mapping
    )
    moved = ["fields.username", "login.username", "fields.PIN", "fields.Pin"]
    assert columns(convert) == ["type", *moved]
    assert columns(template) == moved


def test_the_normalizing_kinds_name_both_names_and_every_merged_column():
    items = [{"itemId": "a", "revisionDate": REVISION}]
    rename = one(kind="renameField", items=items, **{"from": "PIN", "to": "Pin"})
    merge = one(
        kind="mergeColumns",
        sources=["fields.user", "fields.User"],
        target="login.username",
        items=items,
    )
    merge_items = one(
        kind="mergeItems",
        fromItemId="a",
        fromRevisionDate=REVISION,
        toItemId="b",
        toRevisionDate=REVISION,
        mapping={"fields.Recovery codes": "notes", "login.uris": "login.uris"},
        append=["login.uris"],
    )
    assert columns(rename) == ["fields.PIN", "fields.Pin"]
    assert columns(merge) == ["fields.user", "fields.User", "login.username"]
    assert columns(merge_items) == ["fields.Recovery codes", "login.uris"]


def test_whole_item_kinds_name_no_column():
    ops = [
        one(kind="trashItem", itemId="a", revisionDate=REVISION),
        one(kind="restoreItems", backup="b.zip.age", itemIds=["a"], revisionDates={}),
        one(kind="createItem", name="New", type=1, values={"login.username": "x"}),
        one(kind="setToolNoteSection", section="toFill", changes=[]),
    ]
    assert [columns(op) for op in ops] == [[], [], [], []]

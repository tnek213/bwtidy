"""The datatypes section of sections/datatypes.py (docs/spec.md, Datatypes): what an entry needs,
the coordinates of a template that take a datatype and the datatypes templates use."""

from typing import Any

import pytest

from bwtidy.items.patterns import MAX_VALUE_LEN
from bwtidy.sections.datatypes import (
    MAX_EXAMPLES,
    InvalidDatatypes,
    load_datatypes,
    parse_datatypes,
    used_datatypes,
)
from bwtidy.sections.templates import InvalidTemplates, parse_templates, template_list
from tests.support.datatypes_helpers import (
    BOOLEAN,
    DATE,
    DATE_ID,
    HIDDEN,
    LOGIN,
    PIN,
    PIN_ID,
    TEXT,
    URL,
    URL_ID,
    field,
    one,
    path,
    stored,
    template,
)
from tests.support.quiet import assert_absent

# The section


def test_a_valid_section_is_read_with_its_facts():
    datatype = parse_datatypes(
        [
            {
                **PIN,
                "description": "A PIN code",
                "ignoreCase": True,
                "examples": ["1234"],
                "counterExamples": ["abc"],
            }
        ]
    )[0]
    assert datatype.broken is None
    assert datatype.compiled is not None
    assert datatype.compiled.shortest == 4
    assert datatype.dump() == {
        **PIN,
        "description": "A PIN code",
        "ignoreCase": True,
        "examples": ["1234"],
        "counterExamples": ["abc"],
    }


@pytest.mark.parametrize(
    ("entry", "message"),
    [
        ({**PIN, "id": "short"}, "not 8 hex"),
        ({**PIN, "id": "0000AAAA"}, "not 8 hex"),
        ({**PIN, "id": 7}, "id is no text"),
        ({**PIN, "name": " "}, "needs a name"),
        ({**PIN, "pattern": ""}, "needs a pattern"),
        ({**PIN, "pattern": "(?=a)"}, "column"),
        ({**PIN, "ignoreCase": "yes"}, "ignoreCase"),
        ({**PIN, "wholeValue": "no"}, "wholeValue"),
        ({**PIN, "wholeValue": None}, "wholeValue"),
        ({**PIN, "wholeValue": False, "counterExamples": ["x1234"]}, "counter-example 1 matches"),
        ({**PIN, "description": 3}, "description"),
        ({**PIN, "examples": "1234"}, "examples"),
        ({**PIN, "examples": [1234]}, "examples"),
        ({**PIN, "examples": ["1234", "12"]}, "example 2 does not match"),
        ({**PIN, "examples": ["1" * (MAX_VALUE_LEN + 1)]}, "example 1 is too long"),
        ({**PIN, "examples": ["1234"] * (MAX_EXAMPLES + 1)}, "more than"),
        ({**PIN, "counterExamples": ["1234"]}, "counter-example 1 matches"),
        ("no object", "an object"),
    ],
)
def test_an_entry_is_refused_when_saved_and_marked_broken_when_loaded(entry: Any, message: str):
    with pytest.raises(InvalidDatatypes, match=message):
        parse_datatypes([entry])
    loaded = load_datatypes([entry])[0]
    assert loaded.compiled is None
    assert message in (loaded.broken or "")


def test_a_datatype_without_whole_value_matches_the_whole_value():
    whole, stored_true = parse_datatypes([PIN, {**DATE, "wholeValue": True}])
    assert whole.wholeValue and stored_true.wholeValue
    assert "wholeValue" not in whole.dump()
    assert "wholeValue" not in stored_true.dump()
    assert whole.compiled is not None and whole.compiled.whole_value
    with pytest.raises(InvalidDatatypes, match="example 1 does not match"):
        parse_datatypes([{**PIN, "examples": ["id 1234"]}])


def test_whole_value_false_round_trips_and_matches_part_of_a_value():
    entry = {**PIN, "wholeValue": False, "examples": ["id 1234"], "counterExamples": ["12"]}
    datatype = parse_datatypes([entry])[0]
    assert datatype.wholeValue is False
    assert datatype.compiled is not None and not datatype.compiled.whole_value
    assert datatype.dump() == stored(entry)
    assert parse_datatypes([datatype.dump()])[0] == datatype


def test_a_pattern_error_names_its_column():
    with pytest.raises(InvalidDatatypes, match=r"datatypes\[0\] \(PIN\): .*column 3"):
        parse_datatypes([{**PIN, "pattern": "a(?=b)"}])


def test_a_refusal_never_quotes_an_example():
    example = "example-that-does-not-conform"
    with pytest.raises(InvalidDatatypes) as refused:
        parse_datatypes([{**PIN, "examples": [example]}])
    assert_absent(example, str(refused.value), "the error message")


def test_names_are_unique_after_case_folding_and_trimming_and_ids_are_unique():
    other_name = {**DATE, "name": "  pin "}
    with pytest.raises(InvalidDatatypes, match=r"datatypes\[1\].*more than one datatype named"):
        parse_datatypes([PIN, other_name])
    with pytest.raises(InvalidDatatypes, match="more than one datatype with the id"):
        parse_datatypes([PIN, {**DATE, "id": PIN_ID}])
    ## on load the first stays good and the later one is marked
    first, second = load_datatypes([PIN, other_name])
    assert first.broken is None
    assert "more than one" in (second.broken or "")


def test_a_name_is_stored_trimmed():
    assert parse_datatypes([{**PIN, "name": " PIN "}])[0].name == "PIN"


def test_a_section_that_is_no_list_is_refused_and_no_section_is_none():
    with pytest.raises(InvalidDatatypes):
        parse_datatypes({"a": 1})
    with pytest.raises(InvalidDatatypes):
        load_datatypes({"a": 1})
    assert load_datatypes(None) == []


# The coordinates


@pytest.mark.parametrize(
    "coordinate",
    [
        path("name", PIN_ID),
        path("notes", PIN_ID),
        path("login.username", PIN_ID),
        path("login.password", PIN_ID),
        path("login.uris", PIN_ID),
        field("x", TEXT, PIN_ID),
        field("x", HIDDEN, PIN_ID),
    ],
)
def test_text_coordinates_can_have_a_datatype(coordinate: dict[str, Any]):
    assert template_list([one(coordinate, type_=LOGIN)])[0]["coordinates"] == [coordinate]


def test_used_datatypes_are_the_removed_ones_templates_use_in_the_order_of_the_datatypes():
    datatypes = load_datatypes([stored(URL), stored(DATE), stored(PIN)])
    templates = parse_templates(
        [
            template(path("login.username", PIN_ID), name="First", type_=LOGIN),
            template(
                path("login.password", URL_ID), field("x", TEXT, PIN_ID), name="Second", type_=LOGIN
            ),
            template(path("login.uris", DATE_ID), name="Third", type_=LOGIN),
        ]
    )
    used = used_datatypes(templates, {PIN_ID, URL_ID}, datatypes)
    assert [(d.id, names) for d, names in used] == [
        (URL_ID, ["Second"]),
        (PIN_ID, ["First", "Second"]),
    ]


def test_used_datatypes_leave_out_removed_ones_no_template_uses():
    datatypes = load_datatypes([stored(PIN), stored(DATE)])
    templates = parse_templates([template(path("login.username", PIN_ID), type_=LOGIN)])
    assert used_datatypes(templates, {DATE_ID}, datatypes) == []
    assert used_datatypes(templates, set(), datatypes) == []


def test_a_boolean_field_is_refused_naming_it():
    with pytest.raises(InvalidTemplates, match="boolean field Enabled"):
        parse_templates([template(field("Enabled", BOOLEAN, PIN_ID))])


@pytest.mark.parametrize(
    ("coordinate", "named"),
    [
        (path("attachments", PIN_ID), "attachments"),
        (path("favorite", PIN_ID), "favorite"),
        ({"kind": "path", "path": "name", "datatype": 5}, "name"),
        ({"kind": "field", "name": "PIN", "fieldType": TEXT, "datatype": 5}, "fields.PIN"),
    ],
)
def test_what_is_a_number_or_no_text_is_refused_naming_the_coordinate(
    coordinate: dict[str, Any], named: str
):
    with pytest.raises(InvalidTemplates, match=named):
        parse_templates([template(coordinate)])


def test_a_coordinate_without_a_datatype_is_stored_as_it_always_was():
    plain = template(field("x"), path("notes"))
    assert template_list(parse_templates([plain])) == [plain]

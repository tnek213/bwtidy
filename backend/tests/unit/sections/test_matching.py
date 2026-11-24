"""Templates: validation, duck typed matching, fill scores with and without datatypes, field name
patterns and the plan of applyTemplate."""

import copy
import json
from typing import Any

import pytest

from bwtidy.items.coordinates import Lookups
from bwtidy.items.patterns import MAX_VALUE_LEN
from bwtidy.sections import dump_sections
from bwtidy.sections.datatypes import Datatype, load_datatypes, parse_datatypes
from bwtidy.sections.matching import (
    best_match,
    duplicate_warnings,
    fill_score,
    floored_hundredths,
    matches,
    scores,
)
from bwtidy.sections.templates import InvalidTemplates, Template, load_templates, parse_templates
from bwtidy.writes.planners import plan_template
from tests.support.datatypes_helpers import (
    BOOLEAN,
    COMPUTER,
    CONFORMING,
    DATE,
    DATE_ID,
    HIDDEN,
    MEDIA_PC,
    NOTE_WITH_USER,
    OFF_FORMAT,
    PIN,
    PIN_ID,
    TEXT,
    THINKPAD,
    URL,
    URL_ID,
    WEB_LOGIN,
    a_login,
    field,
    item,
    note,
    one,
    path,
    template,
)
from tests.support.datatypes_helpers import LOGIN as LOGIN_TYPE
from tests.support.sample_vault import COLLECTIONS, FOLDERS, LOGIN, ORGANIZATIONS

LOOKUPS = Lookups.build(FOLDERS, ORGANIZATIONS, COLLECTIONS)


def templates(*raw: dict[str, Any]) -> list[Template]:
    return parse_templates(list(raw))


@pytest.mark.parametrize(
    ("raw", "message"),
    [
        ([COMPUTER, {**NOTE_WITH_USER, "name": "computer "}], "more than one template named"),
        ([{**COMPUTER, "type": 9}], "unknown item type 9"),
        ([{**COMPUTER, "coordinates": [path("login.username")]}], "a note has no login.username"),
        ([{**WEB_LOGIN, "coordinates": [path("card.number")]}], "a login has no card.number"),
        ([{**WEB_LOGIN, "coordinates": [path("folder")]}], "folder cannot be a template"),
        ([{**WEB_LOGIN, "coordinates": [path("login.nope")]}], "unknown coordinate"),
        ([{**COMPUTER, "coordinates": [field("user"), field("user", 1)]}], "listed twice"),
        ([{**COMPUTER, "coordinates": [field("")]}], "needs a name"),
        ([{**COMPUTER, "name": "  "}], "needs a name"),
        ([{**COMPUTER, "coordinates": [field("user", 3)]}], "fieldType"),
        ([{"name": "Bare"}], "type"),
        ({"templates": []}, "must be a list"),
    ],
)
def test_invalid_templates_are_refused(raw: Any, message: str):
    with pytest.raises(InvalidTemplates, match=message):
        parse_templates(raw)


def test_templates_round_trip_through_the_tool_note_json():
    parsed = templates(COMPUTER, WEB_LOGIN, {**NOTE_WITH_USER, "coordinates": [field("ü")]})
    stored = dump_sections({"version": 1, "templates": [t.model_dump() for t in parsed]})
    assert json.loads(stored)["version"] == 1
    assert json.loads(stored)["templates"][0] == COMPUTER
    assert load_templates(stored) == parsed
    assert load_templates(None) == []
    assert load_templates("  ") == []
    with pytest.raises(InvalidTemplates, match="not JSON"):
        load_templates("free text")
    with pytest.raises(InvalidTemplates, match="version 1"):
        load_templates('{"templates": []}')


def test_a_description_round_trips_and_an_empty_one_is_not_dumped():
    described = {**COMPUTER, "description": "A laptop or desktop with its passwords"}
    parsed = templates(described, NOTE_WITH_USER)
    assert [t.description for t in parsed] == [described["description"], ""]
    dumped = [t.model_dump() for t in parsed]
    assert dumped == [described, NOTE_WITH_USER]
    assert "description" not in dumped[1]
    assert templates({**NOTE_WITH_USER, "description": ""})[0].model_dump() == NOTE_WITH_USER
    stored = dump_sections({"version": 1, "templates": dumped})
    assert load_templates(stored) == parsed


def test_a_stored_template_without_a_description_reads_as_empty():
    assert templates(COMPUTER)[0].description == ""


def test_a_description_holds_at_most_500_characters():
    assert templates({**COMPUTER, "description": "x" * 500})[0].description == "x" * 500
    with pytest.raises(InvalidTemplates, match="A description holds at most 500 characters"):
        templates({**COMPUTER, "description": "x" * 501})
    with pytest.raises(InvalidTemplates, match="description"):
        templates({**COMPUTER, "description": 3})


def test_a_description_does_not_change_what_matches():
    plain, described = (templates(t)[0] for t in (COMPUTER, {**COMPUTER, "description": "Any"}))
    item = {**THINKPAD, "type": 2}
    assert fill_score(item, plain) == fill_score(item, described)


def test_name_notes_and_the_slots_of_the_type_are_allowed():
    templates(
        {"name": "Card", "type": 3, "coordinates": [path("name"), path("card.number")]},
        {"name": "Key", "type": 5, "coordinates": [path("notes"), path("sshKey.privateKey")]},
        {"name": "Empty", "type": 4, "coordinates": []},
    )


def test_an_item_matches_when_the_type_fits_and_every_coordinate_is_filled():
    computer, web = templates(COMPUTER, WEB_LOGIN)
    assert matches(MEDIA_PC, [computer, web]) is None
    full = copy.deepcopy(MEDIA_PC)
    full["fields"] += [
        {"name": "BIOS password", "value": "x", "type": 1},
        {"name": "disk encryption", "value": "y", "type": 1},
    ]
    assert matches(full, [web, computer]) is computer
    ## an empty value is no value
    full["fields"][-1]["value"] = ""
    assert matches(full, [computer]) is None
    assert matches(LOGIN, [computer, web]) is web


def test_the_template_with_most_coordinates_wins_among_full_matches():
    computer, user_only = templates(COMPUTER, NOTE_WITH_USER)
    full = copy.deepcopy(MEDIA_PC)
    full["fields"] += [
        {"name": "BIOS password", "value": "x", "type": 1},
        {"name": "disk encryption", "value": "y", "type": 1},
    ]
    assert matches(full, [user_only, computer]) is computer
    assert matches(full, [computer, user_only]) is computer
    assert matches(MEDIA_PC, [user_only, computer]) is user_only
    ## a tie goes to the first listed
    twin = Template.model_validate({**NOTE_WITH_USER, "name": "Twin"})
    assert matches(MEDIA_PC, [twin, user_only]) is twin


def test_fill_score_counts_the_filled_coordinates_and_the_type():
    computer, web = templates(COMPUTER, WEB_LOGIN)
    score = fill_score(MEDIA_PC, computer)
    assert (score.score, score.typeMatches, score.filled, score.total) == (0.6, True, 3, 5)
    ## ThinkPad has none of the exact field names, case counts
    score = fill_score(THINKPAD, computer)
    assert (score.score, score.typeMatches, score.filled, score.total) == (0.0, False, 0, 5)
    score = fill_score(LOGIN, computer)
    assert score.typeMatches is False
    score = fill_score(THINKPAD, web)
    assert (score.filled, score.total, score.typeMatches) == (2, 4, True)
    ## a note never fills a login slot
    score = fill_score(MEDIA_PC, web)
    assert (score.score, score.typeMatches, score.filled) == (0.0, False, 0)


def test_a_near_miss_names_the_coordinates_it_lacks():
    computer, web = templates(COMPUTER, WEB_LOGIN)
    assert fill_score(MEDIA_PC, computer).missing == (
        "fields.BIOS password",
        "fields.disk encryption",
    )
    ## the type is not a coordinate, so a type mismatch alone adds nothing to missing
    assert fill_score(THINKPAD, web).missing == ("login.username", "login.uris")
    full = copy.deepcopy(MEDIA_PC)
    full["fields"] += [
        {"name": "BIOS password", "value": "x", "type": 1},
        {"name": "disk encryption", "value": "y", "type": 1},
    ]
    assert fill_score(full, computer).missing == ()
    assert fill_score({**full, "type": 1}, computer).missing == ()


def test_the_type_counts_as_one_of_the_things_to_match():
    [computer] = templates(COMPUTER)
    full = copy.deepcopy(MEDIA_PC)
    full["fields"] += [
        {"name": "BIOS password", "value": "x", "type": 1},
        {"name": "disk encryption", "value": "y", "type": 1},
    ]
    score = fill_score(full, computer)
    assert (score.filled, score.total, score.score, score.full) == (5, 5, 1.0, True)
    ## every coordinate filled but the wrong type is a near miss, not a match
    login = {**copy.deepcopy(full), "type": 1, "login": {}}
    score = fill_score(login, computer)
    assert (score.filled, score.total, score.typeMatches, score.full) == (4, 5, False, False)
    assert matches(login, [computer]) is None
    ## a template without coordinates is matched by its type alone
    empty = Template.model_validate({"name": "Any note", "type": 2, "coordinates": []})
    assert (fill_score(MEDIA_PC, empty).filled, fill_score(MEDIA_PC, empty).total) == (1, 1)
    assert matches(MEDIA_PC, [empty]) is empty
    assert fill_score(LOGIN, empty).score == 0.0


# Optional coordinates


PINS = parse_datatypes([{"id": PIN_ID, "name": "PIN", "pattern": r"\d{4}"}])


def optional(coordinate: dict[str, Any]) -> dict[str, Any]:
    return {**coordinate, "optional": True}


def pin(coordinate: dict[str, Any]) -> dict[str, Any]:
    return {**coordinate, "datatype": PIN_ID}


def test_an_item_lacking_an_optional_field_is_a_full_match_scored_without_it():
    [plain, loose] = templates(
        {"name": "Plain", "type": 2, "coordinates": [field("user")]},
        {"name": "Loose", "type": 2, "coordinates": [field("user"), optional(field("Serial"))]},
    )
    for noted in (note(("user", "a")), note(("user", "a"), ("Serial", "SN-9"))):
        score = fill_score(noted, loose)
        assert (score.filled, score.total, score.missing, score.full) == (2, 2, (), True)
        assert (score.filled, score.total) == (fill_score(noted, plain).filled, 2)
    ## the optional field never shows as missing, also when a required one is
    score = fill_score(note(), loose)
    assert (score.filled, score.total, score.missing) == (1, 2, ("fields.user",))


def test_an_optional_value_off_its_datatype_makes_a_near_miss_and_is_not_missing():
    [loose] = templates(
        {"name": "Loose", "type": 2, "coordinates": [field("user"), optional(pin(field("PIN")))]}
    )
    score = fill_score(note(("user", "a"), ("PIN", "12x4")), loose, PINS)
    assert score.total == 3
    assert 2 < score.filled < 3
    assert not score.full
    assert score.missing == ()
    assert [entry.column for entry in score.formats] == ["fields.PIN"]
    ## without a value it adds no term
    score = fill_score(note(("user", "a")), loose, PINS)
    assert (score.filled, score.total, score.formats, score.full) == (2, 2, (), True)


def test_an_optional_value_that_fits_adds_its_format_term():
    [loose] = templates(
        {"name": "Loose", "type": 2, "coordinates": [field("user"), optional(pin(field("PIN")))]}
    )
    score = fill_score(note(("user", "a"), ("PIN", "1234")), loose, PINS)
    assert (score.filled, score.total, score.full) == (3, 3, True)
    assert score.formats[0].score == 1


def test_best_match_goes_by_required_then_format_terms_then_optional_filled():
    def best(item: dict[str, Any], *raw: dict[str, Any]) -> str | None:
        listed = templates(*raw)
        found = best_match(listed, scores(item, listed, PINS))
        return found.name if found else None

    full = note(("a", "1"), ("b", "1"), ("PIN", "1234"))
    two_required = {"name": "Two", "type": 2, "coordinates": [field("a"), field("b")]}
    one_and_optionals = {
        "name": "One",
        "type": 2,
        "coordinates": [field("a"), optional(field("b")), optional(pin(field("PIN")))],
    }
    assert best(full, one_and_optionals, two_required) == "Two"
    one_formatted = {
        "name": "Formatted",
        "type": 2,
        "coordinates": [field("a"), optional(pin(field("PIN")))],
    }
    one_optional = {
        "name": "Optional",
        "type": 2,
        "coordinates": [field("a"), optional(field("b"))],
    }
    assert best(full, one_optional, one_formatted) == "Formatted"
    one_plain = {"name": "Plain", "type": 2, "coordinates": [field("a")]}
    assert best(full, one_plain, one_optional) == "Optional"
    ## an optional field the item lacks counts for nothing, so the first listed wins the tie
    assert best(note(("a", "1")), one_optional, one_plain) == "Optional"
    assert best(note(("a", "1")), one_plain, one_optional) == "Plain"


def test_a_template_with_only_optional_coordinates_is_refused():
    raw = {"name": "Loose", "type": 2, "coordinates": [optional(field("user"))]}
    with pytest.raises(InvalidTemplates, match="at least one required field"):
        parse_templates([raw])
    ## name may be optional like any coordinate
    templates({**raw, "coordinates": [optional(path("name")), field("user")]})


def test_optional_must_be_a_boolean():
    raw = {"name": "Loose", "type": 2, "coordinates": [field("a"), {**field("b"), "optional": 1}]}
    with pytest.raises(InvalidTemplates, match="boolean"):
        parse_templates([raw])


def test_a_stored_template_without_the_key_reads_as_required_and_dumps_without_it():
    [computer] = templates(COMPUTER)
    assert not any(c.optional for c in computer.coordinates)
    assert computer.model_dump() == COMPUTER
    loose = {"name": "Loose", "type": 2, "coordinates": [field("user"), optional(field("x"))]}
    [parsed] = templates(loose)
    assert parsed.model_dump() == loose
    ## false is the default, so it is left out
    [parsed] = templates(
        {**loose, "coordinates": [field("user"), {**field("x"), "optional": False}]}
    )
    assert parsed.model_dump()["coordinates"][1] == field("x")


def test_apply_creates_an_optional_field_empty_and_maps_onto_it():
    coordinates = [field("user"), optional(field("BIOS password", HIDDEN))]
    loose = {"name": "Loose computer", "type": 2, "coordinates": coordinates}
    [computer] = templates(loose)
    new = plan_template(MEDIA_PC, computer, {}, LOOKUPS)
    assert {"name": "BIOS password", "value": None, "type": 1, "linkedId": None} in new["fields"]
    new = plan_template(MEDIA_PC, computer, {"fields.BIOS password": "fields.Serial"}, LOOKUPS)
    assert {"name": "BIOS password", "value": "SN-9", "type": 1, "linkedId": None} in new["fields"]


def test_apply_on_the_same_type_moves_values_and_adds_missing_fields_empty():
    [computer] = templates(COMPUTER)
    new = plan_template(
        MEDIA_PC,
        computer,
        {"fields.disk encryption": "fields.dm-crypt password", "fields.user": "fields.user"},
        LOOKUPS,
    )
    assert new["fields"] == [
        {"name": "user", "value": "media", "type": 0, "linkedId": None},
        {"name": "user password", "value": "media-secret-41", "type": 1, "linkedId": None},
        {"name": "Serial", "value": "SN-9", "type": 0, "linkedId": None},
        {"name": "disk encryption", "value": "disk-secret-42", "type": 1, "linkedId": None},
        {"name": "BIOS password", "value": None, "type": 1, "linkedId": None},
    ]
    ## every other key survives untouched
    assert {k: v for k, v in new.items() if k != "fields"} == {
        k: v for k, v in MEDIA_PC.items() if k != "fields"
    }
    assert matches(new, [computer]) is None
    new["fields"][-1]["value"] = "set later"
    assert matches(new, [computer]) is computer


def test_apply_on_the_same_type_moves_built_in_leaves_and_notes():
    [web] = templates(WEB_LOGIN)
    new = plan_template(THINKPAD, web, {"login.username": "fields.User"}, LOOKUPS)
    assert new["login"]["username"] == "dev"
    assert "User" not in [f["name"] for f in new["fields"]]
    [notes_template] = templates(
        {"name": "Notes", "type": 2, "coordinates": [field("comment"), field("Title")]}
    )
    new = plan_template(
        MEDIA_PC, notes_template, {"fields.comment": "notes", "fields.Title": "name"}, LOOKUPS
    )
    assert new["notes"] is None
    assert new["name"] == "Media PC"
    assert new["fields"][-2:] == [
        {"name": "comment", "value": "Under the TV", "type": 0, "linkedId": None},
        {"name": "Title", "value": "Media PC", "type": 0, "linkedId": None},
    ]


def test_apply_with_another_type_converts_with_the_template_field_types():
    [computer] = templates(COMPUTER)
    new = plan_template(
        THINKPAD,
        computer,
        {
            "fields.user": "fields.User",
            "fields.user password": "login.password",
            "fields.disk encryption": "fields.LUKS",
            "fields.BIOS password": "fields.BIOS Password",
        },
        LOOKUPS,
    )
    assert new["type"] == 2
    assert new["secureNote"] == {"type": 0}
    assert "login" not in new
    assert new["folderId"] == "f1"
    assert new["fields"] == [
        {"name": "Windows key", "value": "AAAAA-BBBBB", "type": 0, "linkedId": None},
        {"name": "user", "value": "dev", "type": 0, "linkedId": None},
        {"name": "user password", "value": "user-secret-33", "type": 1, "linkedId": None},
        {"name": "disk encryption", "value": "luks-secret-32", "type": 1, "linkedId": None},
        {"name": "BIOS password", "value": "bios-secret-31", "type": 1, "linkedId": None},
    ]
    assert matches(new, [computer]) is computer


def test_a_text_source_may_go_into_a_hidden_template_field():
    [computer] = templates(COMPUTER)
    new = plan_template(THINKPAD, computer, {"fields.BIOS password": "fields.Windows key"}, LOOKUPS)
    [hidden] = [f for f in new["fields"] if f["name"] == "BIOS password"]
    assert hidden == {"name": "BIOS password", "value": "AAAAA-BBBBB", "type": 1, "linkedId": None}


@pytest.mark.parametrize(
    ("item", "mapping", "message"),
    [
        (MEDIA_PC, {"fields.user": "fields.user password"}, "secret fields.user password"),
        (THINKPAD, {"fields.user": "login.password"}, "secret login.password"),
        (THINKPAD, {"fields.user": "fields.LUKS"}, "non-secret fields.user"),
        (MEDIA_PC, {"fields.Serial": "fields.user"}, "no coordinate of the template"),
        (MEDIA_PC, {"fields.user": "folder"}, "cannot be mapped"),
        (MEDIA_PC, {"fields.user": "nope"}, "Unknown coordinate"),
    ],
)
def test_bad_template_mappings_are_refused(
    item: dict[str, Any], mapping: dict[str, str], message: str
):
    [computer] = templates(COMPUTER)
    with pytest.raises(ValueError, match=message):
        plan_template(item, computer, mapping, LOOKUPS)


def alike(name: str, *coordinates: dict[str, Any], item_type: int = 2) -> dict[str, Any]:
    return {"name": name, "type": item_type, "coordinates": list(coordinates)}


def test_a_template_with_the_same_type_and_fields_in_another_order_warns():
    both = templates(
        alike("First", field("a"), field("b", HIDDEN)),
        alike("Second", field("b"), field("a", HIDDEN)),
    )
    assert duplicate_warnings(both, []) == [
        "Second: same type and fields as First, which is listed first and takes every match."
    ]


def test_a_different_type_datatype_or_optional_flag_warns_about_nothing():
    base = alike("Base", field("a"), field("b"))
    others = [
        alike("Type", field("a"), field("b"), item_type=1),
        alike("Datatype", pin(field("a")), field("b")),
        alike("Optional", field("a"), optional(field("b"))),
        alike("Fewer", field("a")),
        alike("More", field("a"), field("b"), field("c")),
    ]
    assert duplicate_warnings(templates(base, *others), PINS) == []


def test_an_unknown_or_broken_datatype_counts_as_no_datatype():
    plain = alike("Plain", field("a"))
    unknown = alike("Unknown", {**field("a"), "datatype": "ffffffff"})
    broken = load_datatypes([{"id": PIN_ID, "name": "PIN", "pattern": "(?=x)"}])
    assert duplicate_warnings(templates(plain, unknown), []) == [
        "Unknown: same type and fields as Plain, which is listed first and takes every match."
    ]
    assert len(duplicate_warnings(templates(plain, alike("Broken", pin(field("a")))), broken)) == 1


def test_three_alike_give_two_warnings_naming_the_first():
    three = templates(*(alike(name, field("a")) for name in ("One", "Two", "Three")))
    assert duplicate_warnings(three, []) == [
        "Two: same type and fields as One, which is listed first and takes every match.",
        "Three: same type and fields as One, which is listed first and takes every match.",
    ]


# Field name patterns


def pattern(text: str, field_type: int = 0) -> dict[str, Any]:
    return {"kind": "fieldPattern", "pattern": text, "fieldType": field_type}


def users(*coordinates: dict[str, Any]) -> dict[str, Any]:
    return {"name": "Users", "type": 2, "coordinates": [field("host"), *coordinates]}


def test_a_required_pattern_counts_once_however_many_fields_fit():
    [template] = templates(users(pattern("user_*")))
    score = fill_score(note(("host", "h"), ("user_dev", "k"), ("user_youth", "y")), template)
    assert (score.filled, score.total, score.missing, score.full) == (3, 3, (), True)
    score = fill_score(note(("host", "h"), ("other", "o")), template)
    assert (score.filled, score.total, score.full) == (2, 3, False)
    assert score.missing == ("fields.user_*",)
    ## a field without a value does not count
    empty = note(("host", "h"))
    empty["fields"].append({"name": "user_dev", "value": "", "type": 0, "linkedId": None})
    assert fill_score(empty, template).missing == ("fields.user_*",)


def test_an_optional_pattern_without_a_field_is_a_full_match():
    [template] = templates(users(optional(pattern("user_*"))))
    score = fill_score(note(("host", "h")), template)
    assert (score.filled, score.total, score.missing, score.full) == (2, 2, (), True)
    assert fill_score(note(("host", "h"), ("user_a", "a")), template).optionalFilled == 1


def test_a_pattern_scores_the_lowest_of_its_fields_and_names_it():
    [template] = templates(users(pin(pattern("user_*"))))
    item = note(("host", "h"), ("user_a", "1234"), ("user_b", "12x4"), ("user_c", "1"))
    score = fill_score(item, template, PINS)
    assert score.total == 4
    assert not score.full
    [entry] = score.formats
    assert (entry.column, entry.score, entry.distance) == ("fields.user_c", 0.25, 3)
    assert score.filled == 3.25
    score = fill_score(note(("host", "h"), ("user_a", "1234"), ("user_b", "5678")), template, PINS)
    assert (score.filled, score.total, score.full) == (4, 4, True)
    ## required without a field it is a term of 0 under the pattern's column
    score = fill_score(note(("host", "h")), template, PINS)
    assert [(e.column, e.score) for e in score.formats] == [("fields.user_*", 0)]
    ## optional without a field it has no term
    [loose] = templates(users(optional(pin(pattern("user_*")))))
    score = fill_score(note(("host", "h")), loose, PINS)
    assert (score.filled, score.total, score.formats) == (2, 2, ())


def test_a_field_an_exact_coordinate_names_is_never_the_patterns():
    [template] = templates(users(pin(field("user_admin")), pin(pattern("user_*"))))
    item = note(("host", "h"), ("user_admin", "x"), ("user_dev", "1234"))
    score = fill_score(item, template, PINS)
    assert [entry.column for entry in score.formats] == ["fields.user_admin", "fields.user_dev"]
    assert score.formats[1].score == 1
    ## the exact field alone leaves the pattern without a field
    score = fill_score(note(("host", "h"), ("user_admin", "1234")), template, PINS)
    assert score.missing == ("fields.user_*",)


def test_a_field_two_patterns_fit_counts_for_the_first_only():
    [template] = templates(users(pattern("user_*"), pattern("*_dev")))
    score = fill_score(note(("host", "h"), ("user_dev", "k")), template)
    assert score.missing == ("fields.*_dev",)
    score = fill_score(note(("host", "h"), ("user_dev", "k"), ("youth_dev", "y")), template)
    assert score.full


def test_a_pattern_ignores_case_and_surrounding_spaces():
    [template] = templates(users(pattern(" User_* ")))
    assert fill_score(note(("host", "h"), ("  USER_dev ", "k")), template).full
    assert not fill_score(note(("host", "h"), ("users_dev", "k")), template).full


def test_a_star_in_a_field_coordinate_is_literal():
    [template] = templates(users(field("user_*")))
    assert not fill_score(note(("host", "h"), ("user_dev", "k")), template).full
    assert fill_score(note(("host", "h"), ("user_*", "k")), template).full


@pytest.mark.parametrize("text", ["user", "*", " ** ", "* *", ""])
def test_a_pattern_needs_a_star_and_another_character(text: str):
    with pytest.raises(InvalidTemplates, match=r"a pattern needs a \* and some other character"):
        parse_templates([users(pattern(text))])


def test_two_patterns_alike_but_for_case_are_refused():
    with pytest.raises(InvalidTemplates, match="listed twice"):
        parse_templates([users(pattern("user_*"), pattern("USER_* "))])
    ## a pattern and a field of that very name are two coordinates
    templates(users(pattern("user_*"), field("user_*")))


def test_a_pattern_is_text_or_hidden():
    with pytest.raises(InvalidTemplates, match="fieldType"):
        parse_templates([users(pattern("user_*", 2))])


def test_a_pattern_dumps_as_stored_and_a_template_without_one_as_before():
    raw = users(pin(pattern("user_*", HIDDEN)), optional(pattern("pc_*")))
    [parsed] = templates(raw)
    assert parsed.model_dump() == raw
    [computer] = templates(COMPUTER)
    assert computer.model_dump() == COMPUTER


def test_a_hidden_field_a_pattern_takes_is_secret_by_its_type_in_the_item():
    [template] = templates(users(pin(pattern("user_*"))))
    item = note(("host", "h"))
    item["fields"].append({"name": "user_dev", "value": "12x4", "type": 1, "linkedId": None})
    score = fill_score(item, template, PINS)
    ## the UI scores it, without a character of it
    assert [entry.column for entry in score.formats] == ["fields.user_dev"]


def test_apply_creates_no_field_for_a_pattern():
    [template] = templates(users(pattern("user_*")))
    new = plan_template(MEDIA_PC, template, {}, LOOKUPS)
    names = [entry["name"] for entry in new["fields"]]
    assert "host" in names
    assert not any("*" in name for name in names)
    assert not fill_score(new, template).full


def test_a_pattern_compares_by_its_text_for_the_duplicate_warning():
    listed = templates(
        alike("First", field("a"), pattern("user_*")),
        alike("Second", field("a"), pattern("USER_*")),
        alike("Third", field("a"), pattern("pc_*")),
        alike("Literal", field("a"), field("user_*")),
    )
    assert duplicate_warnings(listed, []) == [
        "Second: same type and fields as First, which is listed first and takes every match."
    ]


# The scores with datatypes

BROKEN = Datatype(PIN_ID, "PIN", "(", broken="no")


def pin_datatypes() -> list[Datatype]:
    return parse_datatypes([PIN])


def test_a_template_without_datatypes_gives_todays_numbers():
    score = fill_score(item(("x", "1", TEXT)), one(field("x"), field("y")), pin_datatypes())
    assert (score.filled, score.total, score.formats) == (2, 3, ())
    assert type(score.filled) is int
    assert score.score == 2 / 3
    assert score.missing == ("fields.y",)


def test_an_off_format_value_gives_its_score_and_one_more_in_total():
    score = fill_score(
        item(("PIN", OFF_FORMAT, TEXT)), one(field("PIN", TEXT, PIN_ID)), pin_datatypes()
    )
    assert score.total == 3
    assert score.filled == 2.8
    assert score.score == 2.8 / 3
    assert not score.full
    assert score.missing == ()
    entry = score.formats[0]
    assert (entry.column, entry.datatype, entry.score) == ("fields.PIN", "PIN", 0.8)
    assert (entry.distance, entry.firstEdit, entry.expected) == (1, 0, "a digit")
    assert not entry.tooLong


def test_a_conforming_value_is_a_full_match_with_a_whole_number():
    score = fill_score(
        item(("PIN", CONFORMING, TEXT)), one(field("PIN", TEXT, PIN_ID)), pin_datatypes()
    )
    assert (score.filled, score.total) == (3, 3)
    assert type(score.filled) is int
    assert score.full
    assert score.formats[0].score == 1
    assert score.formats[0].distance == 0
    assert score.formats[0].firstEdit is None


def test_a_missing_value_gives_0_even_when_the_pattern_takes_an_empty_value():
    datatypes = parse_datatypes([{**PIN, "pattern": r"\d*"}])
    score = fill_score(item(), one(field("PIN", TEXT, PIN_ID)), datatypes)
    assert (score.filled, score.total) == (1, 3)
    assert score.missing == ("fields.PIN",)
    entry = score.formats[0]
    assert (entry.score, entry.distance, entry.firstEdit, entry.expected) == (0, None, None, None)
    assert not entry.tooLong


def test_login_uris_takes_the_lowest():
    datatypes = parse_datatypes([URL])
    coordinate = one(path("login.uris", URL_ID), type_=LOGIN_TYPE)
    good = fill_score(a_login("https://a.test", "https://b.test"), coordinate, datatypes)
    assert good.full
    mixed = fill_score(a_login("https://a.test", "http://b.test"), coordinate, datatypes)
    expected = 1 - 1 / len("https://b.test")
    assert mixed.formats[0].score == floored_hundredths(expected) / 100
    assert mixed.filled == 2 + mixed.formats[0].score
    assert not mixed.full


def test_one_uri_too_long_makes_the_whole_column_0():
    datatypes = parse_datatypes([URL])
    too_long = "https://" + "a" * MAX_VALUE_LEN
    coordinate = one(path("login.uris", URL_ID), type_=LOGIN_TYPE)
    score = fill_score(a_login("https://a.test", too_long), coordinate, datatypes)
    assert score.formats[0].score == 0
    assert score.formats[0].tooLong


def test_a_value_too_long_gives_0_and_too_long():
    score = fill_score(
        item(("PIN", "1" * (MAX_VALUE_LEN + 1), TEXT)),
        one(field("PIN", TEXT, PIN_ID)),
        pin_datatypes(),
    )
    assert (score.filled, score.total) == (2, 3)
    entry = score.formats[0]
    assert (entry.score, entry.distance, entry.tooLong) == (0, None, True)


def test_a_partial_never_rounds_up_to_a_whole_point():
    datatypes = parse_datatypes([{**PIN, "pattern": "a*"}])
    almost = "a" * (MAX_VALUE_LEN - 1) + "b"
    score = fill_score(item(("PIN", almost, TEXT)), one(field("PIN", TEXT, PIN_ID)), datatypes)
    assert score.formats[0].score == 0.99
    assert score.filled == 2.99
    assert not score.full


def test_terms_floor_to_two_decimals_without_the_noise_of_binary_floats():
    datatypes = parse_datatypes([{**PIN, "pattern": "a*"}])
    ## 1 - 71/100 is a hair below 0.29 in binary
    value = "a" * 29 + "b" * 71
    score = fill_score(item(("PIN", value, TEXT)), one(field("PIN", TEXT, PIN_ID)), datatypes)
    assert score.formats[0].score == 0.29
    assert score.filled == 2.29


def test_a_sum_of_terms_keeps_two_decimals():
    datatypes = parse_datatypes([{**PIN, "pattern": "a*"}, DATE])
    values = (("A", "aab", TEXT), ("B", "abb", TEXT))
    coordinates = (field("A", TEXT, PIN_ID), field("B", TEXT, PIN_ID))
    score = fill_score(item(*values), one(*coordinates), datatypes)
    ## 0.66 and 0.33 on top of 2 coordinates and the type
    assert score.filled == 3.99
    assert score.total == 5


def test_full_needs_every_format_at_1():
    datatypes = parse_datatypes([PIN, DATE])
    coordinates = one(field("PIN", TEXT, PIN_ID), field("When", TEXT, DATE_ID))
    good = item(("PIN", CONFORMING, TEXT), ("When", "2026-10-30", TEXT))
    assert fill_score(good, coordinates, datatypes).full
    bad = item(("PIN", CONFORMING, TEXT), ("When", "2026-10-3", TEXT))
    assert not fill_score(bad, coordinates, datatypes).full


def test_off_format_makes_no_match_and_the_most_coordinates_still_win():
    datatypes = pin_datatypes()
    wide = template(field("a"), field("b"), field("PIN"), name="Wide")
    formatted = template(field("a"), field("PIN", TEXT, PIN_ID), name="Formatted")
    plain = template(field("a"), field("PIN"), name="Plain")
    templates = parse_templates([plain, formatted, wide])
    conforming = item(("a", "1", TEXT), ("b", "1", TEXT), ("PIN", CONFORMING, TEXT))
    assert (found := matches(conforming, templates, datatypes)) is not None
    assert found.name == "Wide"
    ## same coordinates: the one with the format that holds is the more exact one
    without_b = item(("a", "1", TEXT), ("PIN", CONFORMING, TEXT))
    assert (found := matches(without_b, templates, datatypes)) is not None
    assert found.name == "Formatted"
    off = item(("a", "1", TEXT), ("PIN", OFF_FORMAT, TEXT))
    assert (found := matches(off, templates, datatypes)) is not None
    assert found.name == "Plain"


def test_an_unknown_or_broken_datatype_counts_as_none():
    unknown = fill_score(item(("x", "1", TEXT)), one(field("x", TEXT, "ffffffff")), pin_datatypes())
    assert (unknown.filled, unknown.total, unknown.formats) == (2, 2, ())
    broken = fill_score(item(("x", "1", TEXT)), one(field("x", TEXT, PIN_ID)), [BROKEN])
    assert (broken.filled, broken.total, broken.formats) == (2, 2, ())


def test_a_boolean_field_of_the_item_under_a_text_coordinate_scores_0():
    value = item(("PIN", "true", BOOLEAN))
    score = fill_score(value, one(field("PIN", TEXT, PIN_ID)), pin_datatypes())
    assert (score.filled, score.total) == (2, 3)
    assert score.formats[0].distance is None


def test_ignore_case_applies():
    datatypes = parse_datatypes([{**PIN, "pattern": "abc", "ignoreCase": True}])
    score = fill_score(item(("x", "ABC", TEXT)), one(field("x", TEXT, PIN_ID)), datatypes)
    assert score.full


def test_a_template_with_no_type_match_never_is_full():
    score = fill_score(
        item(("x", CONFORMING, TEXT)), one(field("x", TEXT, PIN_ID), type_=1), pin_datatypes()
    )
    assert (score.filled, score.total) == (2, 3)
    assert not score.full

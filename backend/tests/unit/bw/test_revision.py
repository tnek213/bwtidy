"""bw/revision.py: when two revisionDates name the same revision."""

import pytest

from bwtidy.bw.revision import Spellings, instant, same_revision

AT = "2026-10-26T10:15:30"


@pytest.mark.parametrize(
    ("a", "b"),
    [
        ## the rounded and the cut spelling of one instant
        (f"{AT}.337Z", f"{AT}.336Z"),
        (f"{AT}.3366667Z", f"{AT}.336Z"),
        (f"{AT}.3366667Z", f"{AT}.337Z"),
        (f"{AT}.336Z", f"{AT}.336Z"),
        (f"{AT}.336Z", f"{AT}.336+00:00"),
        (f"{AT}.336Z", "2026-10-26T11:15:30.336+01:00"),
        (f"{AT}.999Z", "2026-10-26T10:15:31.000Z"),
    ],
)
def test_one_instant_in_two_spellings_is_the_same_revision(a: str, b: str):
    assert same_revision(a, b)
    assert same_revision(b, a)


@pytest.mark.parametrize(
    ("a", "b"),
    [
        (f"{AT}.337Z", f"{AT}.339Z"),
        (f"{AT}.3366667Z", f"{AT}.338Z"),
        (f"{AT}.336Z", "2026-10-26T10:15:31.336Z"),
        (f"{AT}.336Z", "2026-10-27T10:15:30.336Z"),
    ],
)
def test_a_later_revision_is_another_one(a: str, b: str):
    assert not same_revision(a, b)
    assert not same_revision(b, a)


@pytest.mark.parametrize("odd", [None, "", "yesterday", f"{AT}.336", "2026-13-26T10:15:30Z", 5])
def test_a_value_that_does_not_parse_is_only_itself(odd: object):
    assert same_revision(odd, odd)
    assert not same_revision(odd, f"{AT}.336Z")
    assert not same_revision(f"{AT}.336Z", odd)


def test_none_and_empty_are_not_each_other():
    assert not same_revision(None, "")
    assert not same_revision("", None)


def test_the_instant_keeps_the_digits_up_to_the_nanosecond():
    assert instant(f"{AT}.3366667Z") == instant(f"{AT}.336666700Z")
    assert instant(f"{AT}Z") == instant(f"{AT}.000Z")
    later, earlier = instant(f"{AT}.001Z"), instant(f"{AT}.000Z")
    assert later is not None and earlier is not None
    assert later - earlier == 1_000_000


def test_spellings_keep_the_first_spelling_of_a_revision():
    spellings = Spellings()
    first = {"id": "a", "revisionDate": f"{AT}.337Z"}
    again = {"id": "a", "revisionDate": f"{AT}.336Z"}
    later = {"id": "a", "revisionDate": f"{AT}.339Z"}
    spellings.settle(first)
    spellings.settle(again)
    assert again["revisionDate"] == f"{AT}.337Z"
    spellings.settle(later)
    assert later["revisionDate"] == f"{AT}.339Z"
    back = {"id": "a", "revisionDate": f"{AT}.338Z"}
    spellings.settle(back)
    assert back["revisionDate"] == f"{AT}.339Z"


def test_spellings_leave_other_items_and_odd_answers_alone():
    spellings = Spellings()
    spellings.settle({"id": "a", "revisionDate": f"{AT}.337Z"})
    other = {"id": "b", "revisionDate": f"{AT}.336Z"}
    spellings.settle(other)
    assert other["revisionDate"] == f"{AT}.336Z"
    for odd in [None, [], {"id": "a"}, {"revisionDate": f"{AT}.336Z"}]:
        spellings.settle(odd)
    assert spellings.known == {"a": f"{AT}.337Z", "b": f"{AT}.336Z"}

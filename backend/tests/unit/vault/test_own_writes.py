"""vault/own_writes.py: the chain of bwtidy's own writes, followed in any spelling of a revision."""

from bwtidy.vault.own_writes import OwnWrites

AT = "2026-10-26T10:15:30"
LATER = "2026-10-26T10:15:43.500Z"
LAST = "2026-10-26T10:16:33.000Z"


def test_an_own_write_recorded_in_one_spelling_is_found_in_the_other():
    own = OwnWrites()
    own.record_write("a", f"{AT}.337Z", LATER)
    assert own.written_over("a", f"{AT}.336Z")
    assert own.current_revision("a", f"{AT}.336Z") == LATER
    assert own.current_revision("a", f"{AT}.3366667Z") == LATER


def test_the_chain_is_followed_across_spellings():
    own = OwnWrites()
    own.record_write("a", f"{AT}.336Z", LATER)
    own.record_write("a", "2026-10-26T10:15:43.501Z", LAST)
    assert own.current_revision("a", f"{AT}.337Z") == LAST


def test_another_revision_or_item_is_not_an_own_write():
    own = OwnWrites()
    own.record_write("a", f"{AT}.337Z", LATER)
    assert not own.written_over("a", f"{AT}.339Z")
    assert own.current_revision("a", f"{AT}.339Z") == f"{AT}.339Z"
    assert not own.written_over("b", f"{AT}.337Z")
    assert not own.written_over("a", None)


def test_a_write_that_left_the_same_revision_is_not_recorded():
    own = OwnWrites()
    own.record_write("a", f"{AT}.337Z", f"{AT}.336Z")
    assert own.revisions == {}

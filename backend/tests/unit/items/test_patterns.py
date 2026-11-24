"""The pattern engine (bwtidy/items/patterns.py): what it takes, how it scores, how fast."""

import random
import re
import time
from functools import cache
from itertools import product

import pytest

from bwtidy.items.patterns import (
    END_OF_VALUE,
    MAX_DEPTH,
    MAX_PATTERN_LEN,
    MAX_REPEAT,
    MAX_STATES,
    MAX_VALUE_LEN,
    PatternError,
    ValueTooLong,
    compile_pattern,
)
from tests.support.quiet import assert_absent

ISO_DATE = r"\d{4}-\d{2}-\d{2}"
NOTHING = "[^\u0000-\U0010ffff]"


@pytest.mark.parametrize(
    "pattern,column",
    [
        (r"(?=a)", 2),
        (r"(a)\1", 4),
        (r"\b", 1),
        ("a**", 3),
        ("a*?", 3),
        ("a{", 3),
        ("a{1,2", 6),
        ("a{2,1}", 2),
        ("[abc", 1),
        ("[z-a]", 5),
        ("(ab", 1),
        ("a)", 2),
        ("*a", 1),
        ("a^b", 2),
        ("a$b", 2),
        (f"a{{{MAX_REPEAT + 1}}}", 3),
        ("(" * (MAX_DEPTH + 1) + "a" + ")" * (MAX_DEPTH + 1), MAX_DEPTH + 1),
        (r"[\D]", 2),
        ("\\", 1),
        ("a{٣}", 3),
        ("a{²}", 3),
    ],
)
def test_a_rejected_pattern_names_its_column(pattern: str, column: int) -> None:
    with pytest.raises(PatternError) as caught:
        compile_pattern(pattern)
    assert caught.value.column == column
    assert f"(column {column})" in str(caught.value)


@pytest.mark.parametrize(
    "pattern",
    [
        "x" * (MAX_PATTERN_LEN + 1),
        f"(a{{{MAX_REPEAT}}}){{{MAX_REPEAT}}}",
        "a" * (MAX_STATES + 1),
        NOTHING,
        f"a{NOTHING}",
        f"({NOTHING})+",
    ],
)
def test_a_pattern_too_large_or_impossible_has_no_column(pattern: str) -> None:
    with pytest.raises(PatternError) as caught:
        compile_pattern(pattern)
    assert caught.value.column is None


@pytest.mark.parametrize(
    "pattern",
    [
        f"((){{{MAX_REPEAT}}}){{{MAX_REPEAT}}}",
        f"((a{{0}}){{{MAX_REPEAT}}}){{{MAX_REPEAT}}}",
        "(" * MAX_DEPTH + "a" + ")" * MAX_DEPTH,
        f"a{{{MAX_REPEAT}}}",
        "^a$",
        r"a\$",
        "",
        "()",
        "a|",
    ],
)
def test_taken_patterns(pattern: str) -> None:
    assert compile_pattern(pattern).states <= MAX_STATES


@pytest.mark.parametrize(
    "pattern,states_at_most,max_repeat,shortest",
    [
        (r"\d{4,8}", 10, 8, 4),
        (ISO_DATE, 14, 4, 10),
        ("(a{2}){3}", 12, 3, 6),
        ("a|bc", 5, 0, 1),
        ("a*", 2, 0, 0),
        ("a+", 3, 0, 1),
        (f"a|{NOTHING}", 3, 0, 1),
        ("a{0}", 1, 0, 0),
        (".{0,100}", 102, 100, 0),
    ],
)
def test_facts(pattern: str, states_at_most: int, max_repeat: int, shortest: int) -> None:
    compiled = compile_pattern(pattern)
    assert 0 < compiled.states <= states_at_most
    assert compiled.max_repeat == max_repeat
    assert compiled.shortest == shortest


@pytest.mark.parametrize(
    "pattern,value,distance,score",
    [
        (r"\d{4,8}", "1234", 0, 1.0),
        (r"\d{4,8}", "12345678", 0, 1.0),
        (r"\d{4,8}", " 1234", 1, 0.8),
        (r"\d{4,8}", "123", 1, 0.75),
        (r"\d{4,8}", "", 4, 0.0),
        (ISO_DATE, "2026-10-30", 0, 1.0),
        (ISO_DATE, "x026-10-30", 1, 0.9),
        (ISO_DATE, "2026-1030", 1, 0.9),
        (r"\d{2,3}-\d{5,8}", "+46-7012345", 1, 10 / 11),
        (r"\d{4}", "abc", 4, 0.0),
        (r"\d{4}", "12345", 1, 0.8),
        ("forsaken", "fore", 4, 0.5),
        ("abc", "", 3, 0.0),
        ("abc", "ABC", 3, 0.0),
        ("a*", "", 0, 1.0),
        ("a+", "", 1, 0.0),
        ("a+b", "aaaa", 1, 0.75),
        ("", "", 0, 1.0),
        ("", "abc", 3, 0.0),
        ("^abc$", "ab", 1, 2 / 3),
        ("cat|dog", "cat", 0, 1.0),
        ("cat|dog", "dog", 0, 1.0),
        ("cat|dog", "cot", 1, 2 / 3),
        ("cat|category", "categ", 2, 0.6),
        ("(a+)+", "a" * 40 + "!", 1, 40 / 41),
        ("(a*)*", "aab", 1, 2 / 3),
        ("(a|b)*c", "abab", 1, 0.75),
        ("a{2,}", "a", 1, 0.5),
        ("a{2,}", "aaaaa", 0, 1.0),
        ("(ab){2,3}", "abab", 0, 1.0),
        ("(ab){2,3}", "ababa", 1, 0.8),
        ("(ab){2,3}", "abababab", 2, 0.75),
        (r"\D", "1", 1, 0.0),
        (".", "\n", 1, 0.0),
        (r"\w+@\w+\.se", "user@c8,se", 1, 0.9),
        ("(?:a{1,}.b)?", "ax", 1, 0.5),
        (f"a|{NOTHING}", "b", 1, 0.0),
    ],
)
def test_distance_and_score(pattern: str, value: str, distance: int, score: float) -> None:
    result = compile_pattern(pattern).score(value)
    assert result.distance == distance
    assert result.score == pytest.approx(score)
    assert (result.score == 1.0) == (distance == 0)


@pytest.mark.parametrize(
    "pattern,value,distance,score",
    [
        (r"\d{4}", "ab1234cd", 0, 1.0),
        (r"\d{4}", "id 1234", 0, 1.0),
        ("^AB", "ABxyz", 0, 1.0),
        ("^AB", "xAB", 1, 2 / 3),
        ("cd$", "abcd", 0, 1.0),
        ("cd$", "cdx", 1, 2 / 3),
        ("^cd$", "xcd", 1, 2 / 3),
        ("a\\$", "xa$y", 0, 1.0),
        ("a\\$", "a", 1, 0.5),
        ## the matched part is 123 and one digit is missing, the a, b, c and d around it are free
        (r"\d{4}", "ab123cd", 1, 0.75),
        ## the matched part is abxc
        ("abc", "xxabxcyy", 1, 0.75),
        ("abc", "", 3, 0.0),
        ("a*", "bbb", 0, 1.0),
    ],
)
def test_part_of_the_value(pattern: str, value: str, distance: int, score: float) -> None:
    result = compile_pattern(pattern, whole_value=False).score(value)
    assert result.distance == distance
    assert result.score == pytest.approx(score)


def test_a_whole_value_match_is_the_default() -> None:
    assert compile_pattern(r"\d{4}").whole_value
    assert compile_pattern(r"\d{4}").score("ab1234cd").distance == 4
    assert compile_pattern(r"\d{4}", whole_value=True).score("id 1234").distance == 3
    assert compile_pattern("^ab$", whole_value=False).score("ab").distance == 0


def test_part_of_the_value_needs_no_more_states() -> None:
    for pattern in [ISO_DATE, "(a|b)*c", "^x", "x$", fill("(ab|c)")]:
        whole = compile_pattern(pattern)
        assert compile_pattern(pattern, whole_value=False).states == whole.states


def test_the_first_edit_of_a_part_counts_from_the_start_of_the_value() -> None:
    result = compile_pattern(r"\d{4}", whole_value=False).score("ab123cd")
    assert (result.first_edit, result.expected) == (5, "a digit")
    assert compile_pattern("cd$", whole_value=False).score("cdx").expected == END_OF_VALUE


def test_ignore_case() -> None:
    assert compile_pattern("abc", ignore_case=True).score("ABC").distance == 0
    assert compile_pattern("[A-Z]{2}", ignore_case=True).score("se").distance == 0
    assert compile_pattern("[^a]", ignore_case=True).score("A").distance == 1
    assert compile_pattern(r"\D", ignore_case=True).score("A").distance == 0
    assert compile_pattern("ä", ignore_case=True).score("Ä").distance == 0
    assert compile_pattern("straße", ignore_case=True).score("STRASSE").distance > 0


def test_pattern_and_value_are_nfc_normalized() -> None:
    composed, decomposed = "é", "é"
    assert compile_pattern(decomposed).score(composed).distance == 0
    assert compile_pattern(composed).score(decomposed).distance == 0
    result = compile_pattern("éx").score(decomposed + "y")
    assert (result.distance, result.first_edit) == (1, 1)


@pytest.mark.parametrize(
    "pattern,value,first_edit,expected",
    [
        (r"\d{4,8}", " 1234", 0, "a digit"),
        (r"\d{4,8}", "1234", None, None),
        (r"\d{4}", "12", 2, "a digit"),
        (r"\d{4}", "", 0, "a digit"),
        (r"\d{4}", "12345", 4, END_OF_VALUE),
        (r"\d{4}", "12a4", 2, "a digit"),
        (ISO_DATE, "x026-10-30", 0, "a digit"),
        (ISO_DATE, "2026x10-30", 4, "-"),
        (ISO_DATE, "2026-1030", 7, "-"),
        ("[A-Z]{2}", "S1", 1, "a letter A-Z"),
        ("[a-z]", "1", 0, "a letter a-z"),
        ("[a-zA-Z]", "1", 0, "a letter"),
        ("[abc]", "x", 0, "one of a, b, c"),
        ("[a-f0]", "x", 0, "one of 0, a-f"),
        ("[^abc]", "a", 0, "any character except one of a, b, c"),
        (r"\D", "1", 0, "any character except a digit"),
        (".", "\n", 0, "any character except a newline"),
        (r"\w", "-", 0, "a letter, digit or underscore"),
        (r"\s", "x", 0, "a whitespace character"),
        (r"a\nb", "ab", 1, "a newline"),
        (r"a\tb", "ab", 1, "a tab"),
        ("a b", "ab", 1, "a space"),
        ("a\u0007b", "ab", 1, "U+0007"),
        ("[\u0001-\u001f]", "x", 0, "U+0001-U+001F"),
        ("[acegikmoqsuw]", "x", 0, "one of a, c, e, g, i, k, m, o, ..."),
        ("", "abc", 0, END_OF_VALUE),
        ("a", "ab", 1, END_OF_VALUE),
    ],
)
def test_first_edit_and_expected(
    pattern: str, value: str, first_edit: int | None, expected: str | None
) -> None:
    result = compile_pattern(pattern).score(value)
    assert result.first_edit == first_edit
    assert result.expected == expected


@pytest.mark.parametrize(
    "pattern,value,repeats",
    [
        (r"\d{4,8}", "123456", (6,)),
        (r"\d{4,8}", "1234", (4,)),
        (ISO_DATE, "2026-10-30", (4, 2, 2)),
        (r"(\d{1,3}\.){3}\d{1,3}", "10.0.255.1", (3, 3, 1)),
        ("a{2,}", "aaaaa", (5,)),
        ("a{0,}", "", (0,)),
        ("a{0,}", "aaa", (3,)),
        ("(a{1,}b){2}", "aaabab", (3, 2)),
        ("(ab){1,3}", "abab", (2,)),
        ("(a|b{2,5}){1,4}", "abbba", (3, 3)),
        ("a{0,5}|b{0,5}", "bb", (0, 2)),
        ("a{0}b{2}", "bb", (0, 2)),
        ("a+b*c?", "aabc", ()),
    ],
)
def test_repeats_used(pattern: str, value: str, repeats: tuple[int, ...]) -> None:
    result = compile_pattern(pattern).score(value)
    assert result.distance == 0
    assert result.repeats_used == repeats


def test_steps_count_the_state_updates() -> None:
    compiled = compile_pattern("abc")
    short, long = compiled.score("ab").steps, compiled.score("ab" * 20).steps
    assert 0 < short < long


def test_a_result_holds_nothing_of_the_value() -> None:
    value = "hunter2-secret"
    result = compile_pattern(r"\d+").score(value)
    leaked = value in repr(result) or any(part in repr(result) for part in ("hunter", "secret"))
    assert not leaked


def test_a_value_above_the_limit_is_too_long() -> None:
    compiled = compile_pattern("a*")
    assert compiled.score("a" * MAX_VALUE_LEN).distance == 0
    value = "a" * (MAX_VALUE_LEN + 1)
    with pytest.raises(ValueTooLong) as caught:
        compiled.score(value)
    assert_absent(value, str(caught.value), "the error message")


def test_the_limit_counts_the_normalized_value() -> None:
    compiled = compile_pattern(".*")
    assert compiled.score("é" * MAX_VALUE_LEN).distance == 0
    with pytest.raises(ValueTooLong):
        compiled.score("क़" * MAX_VALUE_LEN)


def random_pattern(rng: random.Random, depth: int = 0) -> str:
    choice = rng.random()
    if depth > 2 or choice < 0.35:
        return rng.choice(["a", "b", "c", "[ab]", "[^a]", ".", r"\."])
    if choice < 0.55:
        return random_pattern(rng, depth + 1) + random_pattern(rng, depth + 1)
    if choice < 0.7:
        return f"(?:{random_pattern(rng, depth + 1)}|{random_pattern(rng, depth + 1)})"
    quantifier = rng.choice(["*", "+", "?", "{2}", "{0,2}", "{1,}"])
    return f"(?:{random_pattern(rng, depth + 1)}){quantifier}"


def random_value(rng: random.Random, longest: int, alphabet: str = "abc.") -> str:
    return "".join(rng.choice(alphabet) for _ in range(rng.randint(0, longest)))


def test_a_full_match_is_what_re_says() -> None:
    rng = random.Random(1)
    for _ in range(1500):
        pattern = random_pattern(rng)
        compiled = compile_pattern(pattern)
        for _ in range(8):
            value = random_value(rng, 8)
            result = compiled.score(value)
            assert (result.distance == 0) == bool(re.fullmatch(pattern, value)), (pattern, value)
            assert (result.first_edit is None) == (result.distance == 0), (pattern, value)
            assert (result.expected is None) == (result.distance == 0), (pattern, value)
            assert 0 <= result.score <= 1, (pattern, value)


def test_ignore_case_is_what_re_says() -> None:
    rng = random.Random(3)
    for _ in range(500):
        pattern = random_pattern(rng)
        compiled = compile_pattern(pattern, ignore_case=True)
        for _ in range(6):
            value = random_value(rng, 6, "aAbBcC.")
            full = bool(re.fullmatch(pattern, value, re.IGNORECASE))
            assert (compiled.score(value).distance == 0) == full, (pattern, value)


def test_a_partial_full_match_is_what_re_search_says() -> None:
    rng = random.Random(4)
    for _ in range(1000):
        pattern = rng.choice(["", "^"]) + random_pattern(rng) + rng.choice(["", "$"])
        compiled = compile_pattern(pattern, whole_value=False)
        for _ in range(8):
            value = random_value(rng, 8)
            result = compiled.score(value)
            assert (result.distance == 0) == bool(re.search(pattern, value)), (pattern, value)
            assert (result.first_edit is None) == (result.distance == 0), (pattern, value)
            assert 0 <= result.score <= 1, (pattern, value)


@cache
def edit_distance(left: str, right: str) -> int:
    row = list(range(len(right) + 1))
    for i, char in enumerate(left, 1):
        previous, row[0] = row[0], i
        for j, other in enumerate(right, 1):
            previous, row[j] = (
                row[j],
                min(row[j] + 1, row[j - 1] + 1, previous + (char != other)),
            )
    return row[-1]


LONGEST_VALUE = 3
ALL_WORDS = [
    "".join(letters)
    for length in range(LONGEST_VALUE + 3)
    for letters in product("abc.", repeat=length)
]


def test_the_distance_is_the_edit_distance_to_the_language() -> None:
    rng = random.Random(2)
    for _ in range(300):
        pattern = random_pattern(rng)
        compiled = compile_pattern(pattern)
        language = [word for word in ALL_WORDS if re.fullmatch(pattern, word)]
        for _ in range(5):
            value = random_value(rng, LONGEST_VALUE)
            result = compiled.score(value)
            words = [word for word in language if len(word) <= len(value) + 2]
            distances = [edit_distance(value, word) for word in words]
            nearest = min(distances, default=None)
            ## a word longer than the ones tried is more than 2 edits away, so it can only
            ## be the true answer when the distance is above 2
            if result.distance <= 2:
                assert nearest == result.distance, (pattern, value)
            elif nearest is not None:
                assert nearest >= result.distance, (pattern, value)
            if result.first_edit is not None and result.distance <= 2:
                kept = value[: result.first_edit]
                pairs = zip(words, distances, strict=True)
                fits = [w for w, d in pairs if d == nearest and w.startswith(kept)]
                assert fits, (pattern, value)


def test_the_partial_distance_is_the_edit_distance_of_the_nearest_part() -> None:
    rng = random.Random(5)
    for _ in range(300):
        pattern = random_pattern(rng)
        compiled = compile_pattern(pattern, whole_value=False)
        language = [word for word in ALL_WORDS if re.fullmatch(pattern, word)]
        for _ in range(5):
            value = random_value(rng, LONGEST_VALUE)
            parts = {value[i:j] for i in range(len(value) + 1) for j in range(i, len(value) + 1)}
            words = [word for word in language if len(word) <= len(value) + 2]
            nearest = min((edit_distance(p, w) for p in parts for w in words), default=None)
            result = compiled.score(value)
            if result.distance <= 2:
                assert nearest == result.distance, (pattern, value)
            elif nearest is not None:
                assert nearest >= result.distance, (pattern, value)


def fill(unit: str) -> str:
    """As many copies of a unit as the limits take."""
    pattern = unit
    while len(pattern) + len(unit) <= MAX_PATTERN_LEN:
        try:
            compile_pattern(pattern + unit)
        except PatternError:
            break
        pattern += unit
    return pattern


## the slowest ones a random search over patterns within the limits found
SLOWEST = [
    pytest.param(fill(r"((((x|\D|b|[ab]))*)*)*"), "ab" * (MAX_VALUE_LEN // 2), id="nested-stars"),
    pytest.param(r"(([^\n]{0,100})*)*", "a" * MAX_VALUE_LEN, id="nested-counted"),
    pytest.param("(a{0,38}|b{0,38}|c{0,38})*", "a" * MAX_VALUE_LEN, id="loop-of-alternatives"),
    pytest.param(r"(\d{0,38}|[a-z]{0,38}|[A-Z]{0,38})", "\n" * MAX_VALUE_LEN, id="alternatives"),
    pytest.param(".{0,100}", "a" * MAX_VALUE_LEN, id="counted-dot"),
]
## about 50 ms unloaded, with room for a machine busy with parallel workers
SLOWEST_BOUND_SECONDS = 0.4


@pytest.mark.parametrize("whole_value", [True, False], ids=["whole", "part"])
@pytest.mark.parametrize("pattern,value", SLOWEST)
def test_the_slowest_pattern_stays_within_the_bound(
    pattern: str, value: str, whole_value: bool
) -> None:
    compiled = compile_pattern(pattern, whole_value=whole_value)
    best = float("inf")
    for _ in range(3):
        started = time.perf_counter()
        result = compiled.score(value)
        best = min(best, time.perf_counter() - started)
    assert best < SLOWEST_BOUND_SECONDS
    ## the same bound without a clock: at most 3 sweeps over the states per position
    assert result.steps <= 3 * compiled.states * (MAX_VALUE_LEN + 1)


def test_an_evil_pattern_is_no_slower_than_any_other() -> None:
    compiled = compile_pattern("(a+)+(a*)*(a|a)*b")
    started = time.perf_counter()
    result = compiled.score("a" * MAX_VALUE_LEN)
    assert time.perf_counter() - started < SLOWEST_BOUND_SECONDS
    assert result.distance == 1

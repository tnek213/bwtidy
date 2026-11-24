"""The pattern engine of the datatypes: how close a value comes to a full match of a pattern.

A pattern is untrusted text, so it never reaches a backtracking regex engine. It compiles to a
small automaton, and the work for one value is bounded by the value's length times the automaton's
states, both capped by the limits below.

The score is an edit distance: d is the least number of inserted, deleted or substituted
characters that turn the value into a full match, and score = 1 - d / max(len(value), shortest
match length, 1). It says that a value matches a format, never that it is valid: a pattern checks
shape, not meaning.

A pattern compiled with whole_value False may match any part of the value: d is then the least
number of edits that turn some part of the value into a full match (the characters before and
after that part are free), and len(value) in the score is the length of that part. A ^ first takes
the free characters before away, a $ last those after.

Syntax, anything else is a PatternError with the column:
  literals, a backslash before any character that is not a letter or digit, \\n \\t \\r \\f \\v
  .                any character except a newline
  [abc] [a-z] [^...]   classes, a ] first in a class is a literal
  \\d \\w \\s          ASCII shorthands, also inside classes
  \\D \\W \\S          their negations, outside classes only
  ( ) (?: )        groups, none of them capture
  |                alternation
  * + ? {n} {n,} {n,m}   quantifiers, no lazy or possessive ones
  ^ first, $ last  anchors: the match starts at the start of the value or ends at its end,
                   which a whole value match does anyway. Anywhere else they are an error.

Nothing here logs or keeps a value. An error message may quote the pattern, never a value.
"""

import unicodedata
from bisect import bisect_right
from collections import deque
from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass, field
from typing import NamedTuple, NoReturn

## one value takes at most about 50 ms with these: the work is sweeps * states * MAX_VALUE_LEN, and
## tests/unit/test_patterns.py holds the slowest patterns a search found
MAX_PATTERN_LEN = 500
MAX_REPEAT = 100
MAX_STATES = 120
MAX_DEPTH = 20
MAX_VALUE_LEN = 1000

LAST_CODE_POINT = 0x10FFFF
END_OF_VALUE = "the end of the value"
MAX_LISTED_RANGES = 8
SHORTEST_SHOWN_RANGE = 3


class PatternError(ValueError):
    """A pattern the engine does not take. The column is 1-based, None when no column fits."""

    def __init__(self, message: str, column: int | None = None) -> None:
        self.message = message
        self.column = column
        super().__init__(message if column is None else f"{message} (column {column})")


class ValueTooLong(ValueError):
    """A value longer than MAX_VALUE_LEN. The caller scores it 0."""


def _show(code: int) -> str:
    names = {9: "a tab", 10: "a newline", 13: "a carriage return", 32: "a space"}
    if code in names:
        return names[code]
    char = chr(code)
    return char if char.isprintable() else f"U+{code:04X}"


@dataclass(frozen=True, slots=True)
class CharSet:
    """A set of code points as sorted, merged, inclusive ranges, optionally negated."""

    ranges: tuple[tuple[int, int], ...]
    negated: bool = False

    @classmethod
    def of(cls, ranges: Iterable[tuple[int, int]], negated: bool = False) -> "CharSet":
        merged: list[tuple[int, int]] = []
        for low, high in sorted(ranges):
            if merged and low <= merged[-1][1] + 1:
                merged[-1] = (merged[-1][0], max(merged[-1][1], high))
            else:
                merged.append((low, high))
        return cls(tuple(merged), negated)

    def _in_ranges(self, char: str) -> bool:
        code = ord(char)
        index = bisect_right(self.ranges, (code, LAST_CODE_POINT + 1))
        return index > 0 and self.ranges[index - 1][1] >= code

    def matches(self, variants: Iterable[str]) -> bool:
        """Whether a character matches, given it and its case variants.

        The negation comes after the variants: with ignore case [^a] takes neither a nor A.
        """
        return any(self._in_ranges(char) for char in variants) != self.negated

    @property
    def is_empty(self) -> bool:
        return self.negated and self.ranges == ((0, LAST_CODE_POINT),)

    def describe(self) -> str:
        """Short readable text for what the set takes, never with a raw control character."""
        if self.negated:
            if self.ranges == ((10, 10),):
                return "any character except a newline"
            return f"any character except {_describe_ranges(self.ranges)}"
        return _describe_ranges(self.ranges)


type Ranges = tuple[tuple[int, int], ...]

DIGITS: Ranges = ((48, 57),)
UPPER: Ranges = ((65, 90),)
LOWER: Ranges = ((97, 122),)
WORD: Ranges = ((48, 57), (65, 90), (95, 95), (97, 122))
SPACE: Ranges = ((9, 13), (32, 32))
SHORTHANDS = {"d": DIGITS, "w": WORD, "s": SPACE}
SIMPLE_ESCAPES = {"n": "\n", "t": "\t", "r": "\r", "f": "\f", "v": "\v"}
DOT = CharSet.of([(10, 10)], negated=True)
RANGE_NAMES: dict[Ranges, str] = {
    DIGITS: "a digit",
    UPPER: "a letter A-Z",
    LOWER: "a letter a-z",
    UPPER + LOWER: "a letter",
    WORD: "a letter, digit or underscore",
    SPACE: "a whitespace character",
}


def _describe_ranges(ranges: Ranges) -> str:
    if ranges in RANGE_NAMES:
        return RANGE_NAMES[ranges]
    parts: list[str] = []
    for low, high in ranges:
        if high - low < SHORTEST_SHOWN_RANGE:
            parts.extend(_show(code) for code in range(low, high + 1))
        else:
            parts.append(f"{_show(low)}-{_show(high)}")
    if len(parts) == 1:
        return parts[0]
    if len(parts) > MAX_LISTED_RANGES:
        parts = [*parts[:MAX_LISTED_RANGES], "..."]
    return f"one of {', '.join(parts)}"


@dataclass(frozen=True, slots=True)
class Empty:
    pass


@dataclass(frozen=True, slots=True)
class Chars:
    chars: CharSet


@dataclass(frozen=True, slots=True)
class Concat:
    items: "tuple[Node, ...]"


@dataclass(frozen=True, slots=True)
class Alternation:
    branches: "tuple[Node, ...]"


@dataclass(frozen=True, slots=True)
class Repeat:
    """A quantifier. counted is the index among the {n} quantifiers, None for * + and ?."""

    item: "Node"
    low: int
    high: int | None
    counted: int | None


type Node = Empty | Chars | Concat | Alternation | Repeat


class _Parser:
    def __init__(self, pattern: str) -> None:
        self.pattern = pattern
        self.at = 0
        self.end = len(pattern)
        self.depth = 0
        self.counted = 0
        self.max_repeat = 0
        self.anchored_start = False
        self.anchored_end = False

    def fail(self, message: str, at: int | None = None) -> NoReturn:
        raise PatternError(message, (self.at if at is None else at) + 1)

    def peek(self) -> str | None:
        return self.pattern[self.at] if self.at < self.end else None

    def parse(self) -> Node:
        if self.pattern.startswith("^"):
            self.at = 1
            self.anchored_start = True
        if self.end > self.at and self.pattern.endswith("$"):
            ## a $ after an odd number of backslashes is a literal
            body = self.pattern[:-1]
            if (len(body) - len(body.rstrip("\\"))) % 2 == 0:
                self.end -= 1
                self.anchored_end = True
        node = self.parse_alternation()
        if self.at < self.end:
            self.fail(f"unexpected {self.pattern[self.at]!r}")
        return node

    def parse_alternation(self) -> Node:
        branches = [self.parse_concat()]
        while self.peek() == "|":
            self.at += 1
            branches.append(self.parse_concat())
        return branches[0] if len(branches) == 1 else Alternation(tuple(branches))

    def parse_concat(self) -> Node:
        items: list[Node] = []
        while (char := self.peek()) is not None and char not in "|)":
            item = self.parse_repeat()
            if not isinstance(item, Empty):
                items.append(item)
        if not items:
            return Empty()
        return items[0] if len(items) == 1 else Concat(tuple(items))

    def at_quantifier(self) -> bool:
        char = self.peek()
        return char is not None and char in "*+?{"

    def parse_repeat(self) -> Node:
        atom = self.parse_atom()
        if not self.at_quantifier():
            return atom
        start = self.at
        low, high, counted = self.parse_quantifier()
        if self.at_quantifier():
            self.fail("lazy, possessive or stacked quantifiers are not supported")
        if high is not None and low > high:
            self.fail("quantifier min is larger than max", start)
        ## no copy of nothing is ever built, so ((){100}){100} costs no time and no states
        if isinstance(atom, Empty) or high == 0:
            return Empty()
        return Repeat(atom, low, high, counted)

    def parse_quantifier(self) -> tuple[int, int | None, int | None]:
        char = self.pattern[self.at]
        self.at += 1
        if char == "*":
            return 0, None, None
        if char == "+":
            return 1, None, None
        if char == "?":
            return 0, 1, None
        low = self.parse_count()
        if low is None:
            self.fail("expected a number after '{' (use \\{ for a literal brace)")
        high: int | None = low
        if self.peek() == ",":
            self.at += 1
            high = self.parse_count()
        elif self.peek() != "}":
            self.fail("expected ',' or '}' in quantifier")
        if self.peek() != "}":
            self.fail("expected '}' to close quantifier")
        self.at += 1
        counted = self.counted
        self.counted += 1
        self.max_repeat = max(self.max_repeat, low, high or 0)
        return low, high, counted

    def parse_count(self) -> int | None:
        start = self.at
        ## isdigit takes superscripts and other scripts, which int() then refuses or misreads
        while self.at < self.end and self.pattern[self.at] in "0123456789":
            self.at += 1
        if start == self.at:
            return None
        count = int(self.pattern[start : self.at])
        if count > MAX_REPEAT:
            self.fail(f"repeat count larger than {MAX_REPEAT}", start)
        return count

    def parse_atom(self) -> Node:
        char = self.pattern[self.at]
        if char == "(":
            return self.parse_group()
        if char == "[":
            return Chars(self.parse_class())
        if char == ".":
            self.at += 1
            return Chars(DOT)
        if char == "\\":
            escaped = self.parse_escape(in_class=False)
            if isinstance(escaped, CharSet):
                return Chars(escaped)
            return Chars(CharSet.of([(ord(escaped), ord(escaped))]))
        if char in "*+?{":
            self.fail("nothing to repeat")
        if char in "^$":
            self.fail("anchors are only allowed at the very start or end")
        self.at += 1
        return Chars(CharSet.of([(ord(char), ord(char))]))

    def parse_group(self) -> Node:
        start = self.at
        self.depth += 1
        if self.depth > MAX_DEPTH:
            self.fail("groups nested too deeply")
        self.at += 1
        if self.pattern.startswith("?:", self.at):
            self.at += 2
        elif self.peek() == "?":
            self.fail("unsupported group type (lookarounds and the like)")
        node = self.parse_alternation()
        if self.peek() != ")":
            self.fail("missing ')'", start)
        self.at += 1
        self.depth -= 1
        return node

    def parse_class(self) -> CharSet:
        start = self.at
        self.at += 1
        negated = self.peek() == "^"
        if negated:
            self.at += 1
        ranges: list[tuple[int, int]] = []
        first = True
        while True:
            if self.at >= self.end:
                self.fail("unterminated character class", start)
            if self.pattern[self.at] == "]" and not first:
                self.at += 1
                return CharSet.of(ranges, negated)
            first = False
            low = self.parse_class_char()
            if isinstance(low, CharSet):
                ranges.extend(low.ranges)
            elif self.peek() == "-" and self.at + 1 < self.end and self.pattern[self.at + 1] != "]":
                self.at += 1
                high = self.parse_class_char()
                if isinstance(high, CharSet):
                    self.fail("a shorthand cannot end a range")
                if ord(high) < ord(low):
                    self.fail("range out of order")
                ranges.append((ord(low), ord(high)))
            else:
                ranges.append((ord(low), ord(low)))

    def parse_class_char(self) -> CharSet | str:
        char = self.pattern[self.at]
        if char == "\\":
            return self.parse_escape(in_class=True)
        self.at += 1
        return char

    def parse_escape(self, in_class: bool) -> CharSet | str:
        start = self.at
        self.at += 1
        if self.at >= self.end:
            self.fail("trailing backslash", start)
        char = self.pattern[self.at]
        self.at += 1
        if char in SHORTHANDS:
            return CharSet.of(SHORTHANDS[char])
        if char.isupper() and char.lower() in SHORTHANDS:
            if in_class:
                self.fail(f"\\{char} is not supported inside [ ]", start)
            return CharSet.of(SHORTHANDS[char.lower()], negated=True)
        if char in SIMPLE_ESCAPES:
            return SIMPLE_ESCAPES[char]
        if not char.isalnum():
            return char
        self.fail(f"unsupported escape \\{char}", start)


@dataclass(frozen=True, slots=True)
class _Mark:
    """What passing a state says about a counted repetition.

    copies is how many copies are done at that point, None for one more than before.
    """

    counted: int
    copies: int | None


@dataclass(slots=True)
class _BuildState:
    eps: list[int] = field(default_factory=list)
    chars: CharSet | None = None
    target: int = -1
    marks: list[_Mark] = field(default_factory=list)


class _Builder:
    """Builds back to front: each fragment gets the state to go on to and returns its entry.

    A state has any number of epsilon moves and at most one character edge. Working out what comes
    next this way needs no state per concatenation, and a copy of a character set in x{0,100} is
    one state: the distance computation does its work per state, so this decides how long a value
    can be.
    """

    def __init__(self) -> None:
        self.states: list[_BuildState] = []

    def new(self) -> tuple[int, _BuildState]:
        if len(self.states) >= MAX_STATES:
            raise PatternError(
                f"pattern too large (more than {MAX_STATES} states after expanding repetitions)"
            )
        state = _BuildState()
        self.states.append(state)
        return len(self.states) - 1, state

    def set_chars(self, state: _BuildState, chars: CharSet, target: int) -> None:
        ## a set nothing can match is no edge at all: no character could be inserted or put there
        if not chars.is_empty:
            state.chars = chars
            state.target = target

    def build(self, node: Node, then: int) -> int:
        match node:
            case Empty():
                return then
            case Chars(chars):
                entry, state = self.new()
                self.set_chars(state, chars, then)
                return entry
            case Concat(items):
                for item in reversed(items):
                    then = self.build(item, then)
                return then
            case Alternation(branches):
                entry, state = self.new()
                state.eps = [self.build(branch, then) for branch in branches]
                return entry
            case Repeat(_, _, _, None):
                return self.build_plain_repeat(node, then)
            case Repeat():
                return self.build_counted(node, then)

    def build_plain_repeat(self, node: Repeat, then: int) -> int:
        if node.high == 1:
            return self.build_copy(node.item, then, skip=then)
        loop, state = self.new()
        if node.low == 1:
            entry = self.build(node.item, loop)
            state.eps = [entry, then]
            return entry
        self.build_into(node.item, state, loop)
        state.eps.append(then)
        return loop

    def build_into(self, item: Node, state: _BuildState, then: int) -> None:
        """One copy of the item that starts in a state that exists already."""
        if isinstance(item, Chars):
            self.set_chars(state, item.chars, then)
        else:
            state.eps.append(self.build(item, then))

    def build_copy(self, item: Node, then: int, skip: int | None) -> int:
        """One copy of the item that goes on to then, and that can be skipped when skip is set."""
        if isinstance(item, Chars):
            entry, state = self.new()
            self.set_chars(state, item.chars, then)
            if skip is not None:
                state.eps.append(skip)
            return entry
        inner = self.build(item, then)
        if skip is None:
            return inner
        entry, state = self.new()
        state.eps = [inner, skip]
        return entry

    def build_counted(self, node: Repeat, then: int) -> int:
        """A {n}, {n,} or {n,m}.

        Every state that is only reached with a copy finished carries a mark, so walking an
        alignment tells how many copies it used.
        """
        counted = node.counted
        assert counted is not None
        top = node.low if node.high is None else node.high
        after, tail = self.new()
        tail.marks.append(_Mark(counted, top))
        if node.high is None:
            tail.eps.append(self.build_open_end(node.item, counted, then))
        else:
            tail.eps.append(then)
        for copy in range(top, 0, -1):
            skip = then if copy > node.low else None
            after = self.build_copy(node.item, after, skip)
            if copy > 1:
                self.states[after].marks.append(_Mark(counted, copy - 1))
        return after

    def build_open_end(self, item: Node, counted: int, then: int) -> int:
        """The loop of a {n,}: each time around passes a state that adds one copy."""
        again, again_state = self.new()
        again_state.marks.append(_Mark(counted, None))
        loop, state = self.new()
        self.build_into(item, state, again)
        state.eps.append(then)
        again_state.eps.append(loop)
        return loop


@dataclass(frozen=True, slots=True)
class FormatResult:
    """How close a value comes to a full match. It holds no character of the value.

    first_edit is the position (in code points of the NFC normalized value) of the first edit on
    one optimal alignment, expected is what that alignment needed there, both None for a full
    match. repeats_used has one number per counted repetition in the order of the pattern: the
    copies the alignment used, for a repetition inside another one the most in any single run.
    steps counts the state updates of the computation.
    """

    distance: int
    score: float
    first_edit: int | None
    expected: str | None
    repeats_used: tuple[int, ...]
    steps: int


## one state of the automaton as the distance loop reads it: (state, its epsilon targets, its
## character target or -1, the index of its character set, the fewest characters left to the end)
type _Step = tuple[int, tuple[int, ...], int, int, int]

type _Cell = tuple[int, int]

## SKIP passes over a character before the matched part, free when the pattern takes part of a value
DELETE, SUBSTITUTE, INSERT, MATCH, EPSILON, SKIP = range(6)


class _Move(NamedTuple):
    kind: int
    source: _Cell
    target: _Cell
    chars: CharSet | None


def _advance(
    program: Sequence[_Step],
    costs: list[int],
    next_costs: list[int],
    hits: Sequence[bool],
    bound: int,
    remaining: int,
) -> int:
    """Settle the costs of one position and push them on to the next. The state updates done.

    States are numbered so that only the moves that close a loop go backwards, so one sweep
    settles a pattern without loops. A sweep sets a flag when it improves a state behind it, and
    then sweeps again, which is needed at most a few times.

    A state is skipped when it cannot stay within the bound: the characters it still needs beyond
    the remaining ones of the value can only come from insertions.
    """
    reach = bound + remaining
    steps = 0
    while True:
        sweep_again = False
        steps += len(program)
        for state, eps, target, chars, left in program:
            cost = costs[state]
            if cost > bound or cost + left > reach:
                steps -= 1
                continue
            edited = cost + 1
            if next_costs[state] > edited:
                next_costs[state] = edited
            for reached in eps:
                if costs[reached] > cost:
                    costs[reached] = cost
                    if reached < state:
                        sweep_again = True
            if target >= 0:
                if costs[target] > edited:
                    costs[target] = edited
                    if target < state:
                        sweep_again = True
                moved = cost if hits[chars] else edited
                if next_costs[target] > moved:
                    next_costs[target] = moved
        if not sweep_again:
            return steps


def _case_variants(char: str) -> tuple[str, ...]:
    return tuple({v for v in (char, char.lower(), char.upper()) if len(v) == 1})


@dataclass(frozen=True, slots=True)
class CompiledPattern:
    """A compiled pattern with its facts, safe to share: score keeps nothing between calls."""

    pattern: str
    ignore_case: bool
    whole_value: bool
    states: int
    max_repeat: int
    shortest: int
    _free_prefix: bool = field(repr=False)
    _free_suffix: bool = field(repr=False)
    _counted: int = field(repr=False)
    _accept: int = field(repr=False)
    _sets: tuple[CharSet, ...] = field(repr=False)
    _program: tuple[_Step, ...] = field(repr=False)
    _eps_into: tuple[tuple[int, ...], ...] = field(repr=False)
    _chars_into: tuple[tuple[tuple[int, int], ...], ...] = field(repr=False)
    _marks: tuple[tuple[_Mark, ...], ...] = field(repr=False)

    def _hits_of(self, char: str) -> tuple[bool, ...]:
        variants = _case_variants(char) if self.ignore_case else (char,)
        return tuple(chars.matches(variants) for chars in self._sets)

    def score(self, value: str) -> FormatResult:
        text = unicodedata.normalize("NFC", value)
        length = len(text)
        if length > MAX_VALUE_LEN:
            raise ValueTooLong(f"value longer than {MAX_VALUE_LEN} characters")
        ## any value becomes the shortest match with this many edits, so no alignment costs more,
        ## and with a free start and end so does the empty part of the value
        partial = self._free_prefix and self._free_suffix
        bound = self.shortest if partial else max(length, self.shortest)
        hits = {char: self._hits_of(char) for char in set(text)}
        no_hits = (False,) * len(self._sets)
        unreached = bound + 1
        costs = [unreached] * self.states
        costs[0] = 0
        tables: list[list[int]] = []
        steps = 0
        for position in range(length + 1):
            next_costs = [unreached] * self.states
            ## a free character before the match is a self loop on the start state
            if self._free_prefix:
                next_costs[0] = 0
            row = hits[text[position]] if position < length else no_hits
            remaining = length - position
            steps += _advance(self._program, costs, next_costs, row, bound, remaining)
            tables.append(costs)
            costs = next_costs
        end = self._match_end(tables, length)
        distance = tables[end][self._accept]
        moves = self._alignment(tables, text, hits, end)
        first_edit, expected = _first_edit(moves)
        matched = end - sum(1 for move in moves if move.kind == SKIP)
        return FormatResult(
            distance=distance,
            score=1.0 if distance == 0 else 1 - distance / max(matched, self.shortest, 1),
            first_edit=first_edit,
            expected=expected,
            repeats_used=self._repeats_used(moves),
            steps=steps,
        )

    def _match_end(self, tables: list[list[int]], length: int) -> int:
        """Where the matched part ends: the end of the value, or with a free end the last position
        where the accept state costs least (the characters after it are free)."""
        if not self._free_suffix:
            return length
        accept = self._accept
        return min(range(length, -1, -1), key=lambda position: tables[position][accept])

    def _before(
        self, cell: _Cell, tables: list[list[int]], text: str, hits: dict[str, tuple[bool, ...]]
    ) -> Iterator[_Move]:
        """The moves that can end in a cell at its cost, the edits first, found one at a time.

        An alignment is found from its end, so taking the edits first leaves them as late in the
        value as possible: 12345 against \\d{4} gets its edit at the 5, not at the 1. Lazily, as the
        end of x{0,100} has a hundred moves into it and is passed once per deleted character.
        """
        position, state = cell
        here = tables[position]
        cost = here[state]
        consumes = position > 0
        before = tables[position - 1] if consumes else here
        row = hits[text[position - 1]] if consumes else ()
        if consumes and before[state] == cost - 1:
            yield _Move(DELETE, (position - 1, state), cell, None)
        for source, chars in self._chars_into[state]:
            if consumes and not row[chars] and before[source] == cost - 1:
                yield _Move(SUBSTITUTE, (position - 1, source), cell, self._sets[chars])
            if here[source] == cost - 1:
                yield _Move(INSERT, (position, source), cell, self._sets[chars])
        if consumes:
            for source, chars in self._chars_into[state]:
                if row[chars] and before[source] == cost:
                    yield _Move(MATCH, (position - 1, source), cell, self._sets[chars])
        for source in self._eps_into[state]:
            if here[source] == cost:
                yield _Move(EPSILON, (position, source), cell, None)
        if consumes and state == 0 and self._free_prefix:
            yield _Move(SKIP, (position - 1, 0), cell, None)

    def _alignment(
        self, tables: list[list[int]], text: str, hits: dict[str, tuple[bool, ...]], end: int
    ) -> list[_Move]:
        """One optimal alignment that ends at end, first move first.

        A depth first search, not a plain walk back: epsilon moves of (a*)* form a cycle of equal
        costs, where the first move that fits can lead back to a cell already seen.
        """
        last = (end, self._accept)
        origin = (0, 0)
        onward: dict[_Cell, _Move | None] = {last: None}
        stack = [(last, self._before(last, tables, text, hits))]
        while stack[-1][0] != origin:
            for move in stack[-1][1]:
                if move.source not in onward:
                    onward[move.source] = move
                    stack.append((move.source, self._before(move.source, tables, text, hits)))
                    break
            else:
                stack.pop()
        moves: list[_Move] = []
        step = onward[origin]
        while step is not None:
            moves.append(step)
            step = onward[step.target]
        return moves

    def _repeats_used(self, moves: list[_Move]) -> tuple[int, ...]:
        run = [0] * self._counted
        used = [0] * self._counted

        def arrive(state: int) -> None:
            for mark in self._marks[state]:
                index = mark.counted
                run[index] = run[index] + 1 if mark.copies is None else mark.copies
                used[index] = max(used[index], run[index])

        arrive(0)
        for move in moves:
            if move.kind not in (DELETE, SKIP):
                arrive(move.target[1])
        return tuple(used)


def _first_edit(moves: list[_Move]) -> tuple[int | None, str | None]:
    for index, move in enumerate(moves):
        if move.kind in (MATCH, EPSILON, SKIP):
            continue
        position = move.source[0]
        if move.chars is not None:
            return position, move.chars.describe()
        ## a deleted character needed nothing itself: what comes next on the alignment is what the
        ## value should have had there
        for later in moves[index + 1 :]:
            if later.chars is not None:
                return position, later.chars.describe()
        return position, END_OF_VALUE
    return None, None


def compile_pattern(
    pattern: str, ignore_case: bool = False, whole_value: bool = True
) -> CompiledPattern:
    """Compile a pattern, or raise a PatternError. Costs little: limits apply before any work.

    whole_value False lets the match cover any part of the value (see the module docstring).
    """
    pattern = unicodedata.normalize("NFC", pattern)
    if len(pattern) > MAX_PATTERN_LEN:
        raise PatternError(f"pattern longer than {MAX_PATTERN_LEN} characters")
    parser = _Parser(pattern)
    node = parser.parse()
    builder = _Builder()
    accept, _ = builder.new()
    start = builder.build(node, accept)
    return _freeze(pattern, ignore_case, whole_value, parser, builder.states, start, accept)


def _distances_to_accept(states: list[_BuildState], accept: int) -> list[int | None]:
    """The fewest characters from each state to accept, None when it cannot get there."""
    into: list[list[tuple[int, int]]] = [[] for _ in states]
    for index, state in enumerate(states):
        for target in state.eps:
            into[target].append((index, 0))
        if state.target >= 0:
            into[state.target].append((index, 1))
    left: list[int | None] = [None] * len(states)
    left[accept] = 0
    queue = deque([accept])
    while queue:
        target = queue.popleft()
        distance = left[target]
        assert distance is not None
        for source, weight in into[target]:
            known = left[source]
            if known is None or distance + weight < known:
                left[source] = distance + weight
                if weight == 0:
                    queue.appendleft(source)
                else:
                    queue.append(source)
    return left


def _reverse_postorder(states: list[_BuildState], start: int, live: list[bool]) -> list[int]:
    """The live states reached from start, ordered so that only loop closing moves go backwards."""
    order: list[int] = []
    seen = {start}
    stack = [(start, iter(_targets(states[start], live)))]
    while stack:
        state, targets = stack[-1]
        for target in targets:
            if target not in seen:
                seen.add(target)
                stack.append((target, iter(_targets(states[target], live))))
                break
        else:
            order.append(state)
            stack.pop()
    order.reverse()
    return order


def _live_eps(state: _BuildState, live: list[bool]) -> list[int]:
    return [target for target in state.eps if live[target]]


def _targets(state: _BuildState, live: list[bool]) -> list[int]:
    targets = _live_eps(state, live)
    if state.target >= 0 and live[state.target]:
        targets.append(state.target)
    return targets


def _freeze(
    pattern: str,
    ignore_case: bool,
    whole_value: bool,
    parser: _Parser,
    states: list[_BuildState],
    start: int,
    accept: int,
) -> CompiledPattern:
    left = _distances_to_accept(states, accept)
    shortest = left[start]
    if shortest is None:
        raise PatternError("pattern can never match anything")
    live = [distance is not None for distance in left]
    order = _reverse_postorder(states, start, live)
    number = {state: index for index, state in enumerate(order)}
    sets: dict[CharSet, int] = {}
    program: list[_Step] = []
    eps_into: list[list[int]] = [[] for _ in order]
    chars_into: list[list[tuple[int, int]]] = [[] for _ in order]
    for index, old in enumerate(order):
        state = states[old]
        distance = left[old]
        assert distance is not None
        eps = tuple(number[target] for target in _live_eps(state, live))
        for target in eps:
            eps_into[target].append(index)
        target = -1
        chars = -1
        if state.chars is not None and live[state.target]:
            target = number[state.target]
            chars = sets.setdefault(state.chars, len(sets))
            chars_into[target].append((index, chars))
        program.append((index, eps, target, chars, distance))
    return CompiledPattern(
        pattern=pattern,
        ignore_case=ignore_case,
        whole_value=whole_value,
        states=len(order),
        max_repeat=parser.max_repeat,
        shortest=shortest,
        _free_prefix=not whole_value and not parser.anchored_start,
        _free_suffix=not whole_value and not parser.anchored_end,
        _counted=parser.counted,
        _accept=number[accept],
        _sets=tuple(sets),
        _program=tuple(program),
        _eps_into=tuple(tuple(sources) for sources in eps_into),
        _chars_into=tuple(tuple(sources) for sources in chars_into),
        _marks=tuple(tuple(states[old].marks) for old in order),
    )

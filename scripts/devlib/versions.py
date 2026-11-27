"""The version: written by hand in backend/pyproject.toml, repeated in frontend/package.json.

The lock files carry generated copies: backend/uv.lock (the bwtidy package, Python's spelling)
and frontend/package-lock.json (its top-level version and packages[""], semver's spelling).
docs/releases.md, the release notes, has an entry for it in semver's spelling and follows the
format of dev/release.md (The release notes), which release_notes_problems checks.
"""

import datetime
import functools
import importlib.util
import json
import re
import tomllib
import zoneinfo
from pathlib import Path
from types import ModuleType
from typing import NamedTuple

from devlib.git import git
from devlib.paths import ROOT


@functools.cache
def _version_module() -> ModuleType:
    ## backend/src/bwtidy/version.py is stdlib only, so it loads here without the backend venv
    spec = importlib.util.spec_from_file_location(
        "bwtidy_version", ROOT / "backend" / "src" / "bwtidy" / "version.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def semver(version: str) -> str:
    """The version as semver.org spells it: 0.1.0a1 is 0.1.0-alpha.1, b is beta, rc is rc."""
    return str(_version_module().semver(version))


def python_version(version: str) -> str:
    """The version as Python spells it, the inverse of semver: 0.1.0-alpha.1 is 0.1.0a1."""
    match = VERSION.fullmatch(version)
    if match is None:
        raise ValueError(
            f"{version!r} is no version as X.Y.Z, optionally -alpha.N, -beta.N or -rc.N"
        )
    major, minor, patch, pre, number = match.groups()
    release = f"{major}.{minor}.{patch}"
    letters = {name: letter for letter, name in _version_module().PRE_RELEASES.items()}
    return release if pre is None else f"{release}{letters[pre]}{number}"


def pyproject_version() -> str:
    """The version in backend/pyproject.toml, the one place it is written."""
    pyproject = tomllib.loads((ROOT / "backend" / "pyproject.toml").read_text())
    return str(pyproject["project"]["version"])


def package_json_version() -> str:
    """The version in frontend/package.json."""
    return str(json.loads((ROOT / "frontend" / "package.json").read_text())["version"])


def uv_lock_version() -> str:
    """The version of the bwtidy package in backend/uv.lock."""
    lock = tomllib.loads((ROOT / "backend" / "uv.lock").read_text())
    for package in lock.get("package", []):
        if package.get("name") == "bwtidy":
            return str(package["version"])
    raise ValueError("backend/uv.lock has no package bwtidy")


def package_lock_versions() -> tuple[str, str]:
    """The top-level version and the version of packages[""] in frontend/package-lock.json."""
    lock = json.loads((ROOT / "frontend" / "package-lock.json").read_text())
    return str(lock["version"]), str(lock["packages"][""]["version"])


RELEASE_NOTES = "docs/releases.md"
## a release is dated by Swedish time, the maintainer's
TIMEZONE = zoneinfo.ZoneInfo("Europe/Stockholm")
RULES = "dev/release.md, The release notes"
REPO_URL = "https://github.com/tnek213/bwtidy"
UNRELEASED = "Unreleased"
UNRELEASED_SENTENCE = "Changes on main that are not yet in a versioned Docker image."
NOTHING_YET = "Nothing yet."
## the warning headings in their order, then the heading of the other changes, which comes last
WARNINGS = ("Breaking changes", "Security", "Deprecated")
OTHER_CHANGES = "Other changes"
SUBHEADINGS = (*WARNINGS, OTHER_CHANGES)
PRE_RELEASE_ORDER = {"alpha": 0, "beta": 1, "rc": 2}

VERSION = re.compile(r"(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)(?:-(alpha|beta|rc)\.(0|[1-9]\d*))?")
HEADING = re.compile(r"(#{1,6})[ \t]+(.*?)(?:[ \t]+#+)?[ \t]*")
BULLET = re.compile(r"[-*+][ \t]+(.*)")
NESTED_BULLET = re.compile(r"[ \t]+[-*+][ \t]+.*")
FENCE = re.compile(r"[ \t]*(```|~~~)")
DATE = re.compile(r"\d{4}-\d{2}-\d{2}")
METADATA = re.compile(
    r"(?P<date>\S+) · \[GitHub release\]\((?P<release>[^()\s]+)\)"
    r"(?: · \[All changes\]\((?P<compare>[^()\s]+)\))?"
)
WITHDRAWN = re.compile(
    r"(?P<date>\S+) · Withdrawn on (?P<withdrawn>\S+), "
    r"see \[(?P<by>[^\[\]]+)\]\(#(?P<anchor>[^()\s]+)\)"
)
PULL_LINK = r"\[#(\d+)\]\(" + re.escape(REPO_URL) + r"/pull/(\d+)\)"
PULL_GROUP = re.compile(rf"\(({PULL_LINK}(?:, {PULL_LINK})*)\)$")


class Block(NamedTuple):
    """A piece of a section: a paragraph, a bullet, a ### heading or a code block.

    line is where it starts, text its lines joined by one space. A bullet's nested is the line of
    its first nested bullet, 0 without one.
    """

    kind: str
    line: int
    text: str
    nested: int = 0


class Section(NamedTuple):
    """A ## heading (its line and title) with the blocks below it."""

    line: int
    title: str
    blocks: list[Block]


def version_key(version: str) -> tuple[int, ...]:
    """Semantic Versioning's precedence: a pre-release comes before its release."""
    match = VERSION.fullmatch(version)
    assert match is not None
    major, minor, patch, pre, number = match.groups()
    rank = (1, 0, 0) if pre is None else (0, PRE_RELEASE_ORDER[pre], int(number))
    return (int(major), int(minor), int(patch), *rank)


def day_of(timestamp: int) -> str:
    """The date (YYYY-MM-DD) of a Unix time in TIMEZONE."""
    return datetime.datetime.fromtimestamp(timestamp, TIMEZONE).date().isoformat()


def today() -> datetime.date:
    """Today's date in TIMEZONE."""
    return datetime.datetime.now(TIMEZONE).date()


def tag_dates(repo: Path) -> dict[str, str]:
    """The date (YYYY-MM-DD, Swedish time) of the commit each tag v* of repo points at.

    Empty when the checkout has no such tag or git cannot read it.
    """
    listed = git("tag", "--list", "v*", repo=repo, check=False)
    if listed.returncode != 0:
        return {}
    dates = {}
    for tag in listed.stdout.split():
        shown = git("log", "-1", "--format=%ct", f"{tag}^{{commit}}", repo=repo, check=False)
        if shown.returncode == 0 and shown.stdout.strip().isdigit():
            dates[tag] = day_of(int(shown.stdout))
    return dates


def _parse(lines: list[str]) -> tuple[list[tuple[int, str]], list[Section]]:
    """The lines above the first ## heading and the ## sections, in blocks.

    A fenced code block is one block and its lines are never headings. A line indented under a
    bullet, or right below it, continues that bullet.
    """
    preamble: list[tuple[int, str]] = []
    sections: list[Section] = []
    current: Block | None = None
    blank = True
    fence = ""

    def close() -> None:
        nonlocal current
        if current is not None and sections:
            sections[-1].blocks.append(current)
        current = None

    for number, line in enumerate(lines, start=1):
        if fence:
            if line.strip().startswith(fence):
                fence = ""
            continue
        opened = FENCE.match(line)
        if opened:
            fence = opened.group(1)
            in_bullet = current is not None and current.kind == "bullet" and line[:1].isspace()
            if not in_bullet:
                close()
                if sections:
                    current = Block("code", number, "")
                    close()
            blank = False
            continue
        if not sections and not line.startswith("## "):
            preamble.append((number, line))
            continue
        if not line.strip():
            if current is not None and current.kind == "paragraph":
                close()
            blank = True
            continue
        heading = HEADING.fullmatch(line)
        bullet = BULLET.fullmatch(line)
        if heading and len(heading.group(1)) == 2:
            close()
            sections.append(Section(number, heading.group(2), []))
        elif heading:
            close()
            current = Block(f"h{len(heading.group(1))}", number, heading.group(2))
            close()
        elif bullet:
            close()
            current = Block("bullet", number, bullet.group(1).strip())
        elif current is not None and current.kind == "bullet" and (line[:1].isspace() or not blank):
            nested = current.nested or (number if NESTED_BULLET.fullmatch(line) else 0)
            current = current._replace(text=f"{current.text} {line.strip()}", nested=nested)
        elif current is not None and current.kind == "paragraph":
            current = current._replace(text=f"{current.text} {line.strip()}")
        else:
            close()
            current = Block("paragraph", number, line.strip())
        blank = False
    close()
    return preamble, sections


def sections(text: str) -> list[Section]:
    """The ## sections of the release notes in text, in blocks."""
    return _parse(text.splitlines())[1]


def anchor(title: str) -> str:
    """The anchor of a ## heading on the docs site: Python-Markdown's default slug."""
    slug = re.sub(r"[^\w\s-]", "", title).strip().lower()
    return re.sub(r"[-\s]+", "-", slug)


def withdrawn(section: Section) -> bool:
    """Whether the metadata line of a version says it was withdrawn after its release."""
    first = _metadata_block(section)
    return first is not None and WITHDRAWN.fullmatch(first.text) is not None


def withdrawn_versions(text: str) -> set[str]:
    """The versions of the release notes in text that were withdrawn."""
    return {section.title for section in sections(text) if withdrawn(section)}


def metadata_date(section: Section) -> str | None:
    """The date of a version's metadata line, None when it has no such line."""
    first = _metadata_block(section)
    match = METADATA.fullmatch(first.text) if first is not None else None
    return match.group("date") if match is not None else None


def _changes_problems(blocks: list[Block], paragraphs: bool) -> list[tuple[int, str]]:
    """What breaks the rules of the changes of an entry: its bullets and ### headings.

    paragraphs allows a paragraph (or a code block) above the first bullet and heading.
    """
    problems: list[tuple[int, str]] = []
    titles = [block.text for block in blocks if block.kind == "h3"]
    warned = any(title in WARNINGS for title in titles)
    last = -1
    started = False
    under: Block | None = None
    bullets_under = 0

    def close_heading() -> None:
        if under is not None and bullets_under == 0:
            problems.append((under.line, f"### {under.text} has no bullets, leave it out"))

    for block in blocks:
        if block.kind in ("paragraph", "code"):
            if not paragraphs:
                problems.append((block.line, f"## {UNRELEASED} holds only bullets below its line"))
            elif started:
                problems.append(
                    (block.line, "a paragraph of an entry goes above its bullets and headings")
                )
        elif block.kind == "bullet":
            started = True
            bullets_under += 1
            if warned and under is None:
                problems.append(
                    (
                        block.line,
                        f"an entry with a warning heading has its other changes under "
                        f"### {OTHER_CHANGES}",
                    )
                )
            if block.nested:
                problems.append((block.nested, "the changes are one flat list, no nested bullets"))
            group = PULL_GROUP.search(block.text)
            pairs = re.findall(PULL_LINK, group.group(1)) if group else []
            if not pairs:
                problems.append(
                    (
                        block.line,
                        "a bullet ends with its pull request links in parentheses, as in "
                        f"([#23]({REPO_URL}/pull/23))",
                    )
                )
            elif len(re.findall(PULL_LINK, block.text)) > len(pairs):
                problems.append(
                    (
                        block.line,
                        "a bullet has its pull request links in one pair of parentheses at its end",
                    )
                )
            for shown, linked in pairs:
                if shown != linked:
                    problems.append((block.line, f"the link #{shown} points to pull {linked}"))
        elif block.kind == "h3":
            started = True
            close_heading()
            under, bullets_under = block, 0
            if block.text not in SUBHEADINGS:
                problems.append(
                    (
                        block.line,
                        f"### {block.text} is not a heading of an entry: {', '.join(SUBHEADINGS)}",
                    )
                )
                continue
            index = SUBHEADINGS.index(block.text)
            if index <= last:
                problems.append(
                    (block.line, f"### {block.text} is out of order: {', '.join(SUBHEADINGS)}")
                )
            last = max(last, index)
            if block.text == OTHER_CHANGES and not warned:
                problems.append(
                    (
                        block.line,
                        f"### {OTHER_CHANGES} only follows a warning heading "
                        f"({', '.join(WARNINGS)}), without one the changes are a plain list",
                    )
                )
        else:
            started = True
            problems.append(
                (block.line, "an entry has no headings but ### (Breaking changes and the others)")
            )
    close_heading()
    return problems


def _unreleased_problems(section: Section) -> list[tuple[int, str]]:
    blocks = section.blocks
    first = blocks[0] if blocks else None
    if first is None or first.kind != "paragraph" or first.text != UNRELEASED_SENTENCE:
        line = first.line if first is not None else section.line
        return [(line, f"## {UNRELEASED} starts with the line '{UNRELEASED_SENTENCE}'")]
    rest = blocks[1:]
    nothing = [block for block in rest if block.kind == "paragraph" and block.text == NOTHING_YET]
    if nothing:
        others = [block for block in rest if block not in nothing]
        if others:
            return [
                (
                    others[0].line,
                    f"'{NOTHING_YET}' stands alone under ## {UNRELEASED}: "
                    "remove it when there are bullets",
                )
            ]
        return []
    if not any(block.kind == "bullet" for block in rest):
        return [(section.line, f"## {UNRELEASED} has bullets or the line '{NOTHING_YET}'")]
    return _changes_problems(rest, paragraphs=False)


def _metadata_block(section: Section) -> Block | None:
    """The block below a version heading when it is meant as the metadata line, else None."""
    first = section.blocks[0] if section.blocks else None
    if first is None or first.kind != "paragraph":
        return None
    if DATE.match(first.text) or "[GitHub release]" in first.text:
        return first
    return None


def _metadata_problems(
    section: Section, previous: str | None, tags: dict[str, str] | None, newest: bool
) -> list[tuple[int, str]]:
    """What breaks the rules of a version's metadata line, against its tag when tags is not None.

    previous is the next older version in the file that was not withdrawn, None when there is
    none. tags is None when the checkout has none to compare with.
    """
    version = section.title
    tag = f"v{version}"
    first = _metadata_block(section)
    tagged = tags is not None and tag in tags
    problems: list[tuple[int, str]] = []
    if tags is not None and not newest and not tagged:
        problems.append((section.line, f"no tag {tag}: a version below the newest is released"))
    if first is None:
        if tagged or not newest:
            problems.append(
                (
                    section.line,
                    f"## {version} has no metadata line below it: DATE · [GitHub release](...)",
                )
            )
        return problems
    match = METADATA.fullmatch(first.text)
    if match is None:
        example = f"YYYY-MM-DD · [GitHub release]({REPO_URL}/releases/tag/{tag})"
        if previous is not None:
            example += f" · [All changes]({REPO_URL}/compare/v{previous}...{tag})"
        problems.append((first.line, f"the metadata line reads {example}"))
        return problems
    date, release, compare = match.group("date", "release", "compare")
    try:
        datetime.date.fromisoformat(date)
        valid = bool(DATE.fullmatch(date))
    except ValueError:
        valid = False
    if not valid:
        problems.append((first.line, f"{date} is no date as YYYY-MM-DD"))
    elif tagged and tags is not None and tags[tag] != date:
        problems.append(
            (
                first.line,
                f"the date {date} is not {tags[tag]}, "
                f"the date (Swedish time) of the commit of {tag}",
            )
        )
    wanted = f"{REPO_URL}/releases/tag/{tag}"
    if release != wanted:
        problems.append((first.line, f"the GitHub release link is {wanted}"))
    if previous is None and compare is not None:
        problems.append((first.line, "the oldest version not withdrawn has no All changes link"))
    elif previous is not None:
        wanted = f"{REPO_URL}/compare/v{previous}...{tag}"
        if compare is None:
            problems.append((first.line, f"the metadata line ends with [All changes]({wanted})"))
        elif compare != wanted:
            problems.append(
                (
                    first.line,
                    f"the All changes link is {wanted}, from the next older version not withdrawn",
                )
            )
    return problems


def _date_problem(line: int, date: str) -> tuple[int, str] | None:
    try:
        datetime.date.fromisoformat(date)
        valid = bool(DATE.fullmatch(date))
    except ValueError:
        valid = False
    return None if valid else (line, f"{date} is no date as YYYY-MM-DD")


def _withdrawn_problems(section: Section, newer: list[str]) -> list[tuple[int, str]]:
    """What breaks the rules of a withdrawn version's metadata line.

    newer holds the versions above it in the file. A withdrawn version needs no tag.
    """
    first = _metadata_block(section)
    assert first is not None
    match = WITHDRAWN.fullmatch(first.text)
    assert match is not None
    date, gone, by, target = match.group("date", "withdrawn", "by", "anchor")
    problems = [
        problem
        for problem in (_date_problem(first.line, date), _date_problem(first.line, gone))
        if problem is not None
    ]
    if not problems and gone < date:
        problems.append(
            (first.line, f"the withdrawal on {gone} comes before the release on {date}")
        )
    if by not in newer:
        problems.append((first.line, f"{by} is no version above {section.title} in the file"))
    elif target != anchor(by):
        problems.append((first.line, f"the link to {by} is #{anchor(by)}"))
    return problems


def release_notes_problems(version: str) -> list[str]:
    """Every rule of dev/release.md (The release notes) that docs/releases.md breaks.

    One line for each, `docs/releases.md:LINE: what is wrong (dev/release.md, The release
    notes)`. version is the current one as frontend/package.json spells it, which has an entry.
    The dates are compared with the tags v* of the checkout.
    """
    try:
        text = (ROOT / RELEASE_NOTES).read_text()
    except OSError as error:
        return [f"cannot read {RELEASE_NOTES}: {error.strerror}"]
    preamble, sections = _parse(text.splitlines())
    problems: list[tuple[int, str]] = []
    first = next(((number, line) for number, line in preamble if line.strip()), (1, ""))
    if first[1] != "# Release notes":
        problems.append((first[0], "the page starts with the heading '# Release notes'"))
    unreleased = [section for section in sections if section.title == UNRELEASED]
    if not unreleased:
        line = sections[0].line if sections else len(preamble) or 1
        problems.append((line, f"no ## {UNRELEASED}, which is the first ## heading"))
    elif sections[0] is not unreleased[0]:
        problems.append(
            (unreleased[0].line, f"## {UNRELEASED} is the first ## heading, above every version")
        )
    for section in unreleased[1:]:
        problems.append((section.line, f"## {UNRELEASED} is there once"))
    for section in unreleased[:1]:
        problems.extend(_unreleased_problems(section))

    entries: list[Section] = []
    for section in sections:
        if section.title == UNRELEASED:
            continue
        if VERSION.fullmatch(section.title):
            entries.append(section)
            continue
        leading = VERSION.match(section.title)
        if leading:
            problems.append(
                (
                    section.line,
                    f"a version heading holds the version alone: ## {leading.group(0)}",
                )
            )
        else:
            problems.append(
                (
                    section.line,
                    f"## {section.title} is no version: a ## heading is ## {UNRELEASED} or "
                    "## VERSION, as frontend/package.json spells it",
                )
            )

    seen: dict[str, int] = {}
    for index, entry in enumerate(entries):
        above = entries[index - 1] if index else None
        if entry.title in seen:
            problems.append(
                (entry.line, f"## {entry.title} is there twice, first on line {seen[entry.title]}")
            )
            continue
        seen[entry.title] = entry.line
        if above is not None and version_key(entry.title) > version_key(above.title):
            problems.append(
                (entry.line, f"## {entry.title} is newer than ## {above.title} above it")
            )
    if version not in seen:
        line = entries[0].line if entries else (unreleased[0].line if unreleased else 1)
        problems.append(
            (line, f"no ## {version}, the current version as frontend/package.json spells it")
        )

    tags: dict[str, str] | None = tag_dates(ROOT)
    gone = {entry.title for entry in entries if withdrawn(entry)}
    needs_tags = any(entry.title not in gone for entry in entries[1:]) or (
        len(entries) == 1 and _metadata_block(entries[0]) is not None
    )
    if not tags:
        tags = None
        if needs_tags:
            problems.append(
                (
                    entries[0].line,
                    "the checkout has no tags to check the dates with, fetch them with "
                    "git fetch --tags",
                )
            )
    for index, entry in enumerate(entries):
        if entry.title in gone:
            problems.extend(_withdrawn_problems(entry, [e.title for e in entries[:index]]))
        else:
            older = [e.title for e in entries[index + 1 :] if e.title not in gone]
            previous = older[0] if older else None
            problems.extend(_metadata_problems(entry, previous, tags, newest=index == 0))
        changes = entry.blocks[1:] if _metadata_block(entry) else entry.blocks
        problems.extend(_changes_problems(changes, paragraphs=True))

    problems.sort(key=lambda problem: problem[0])
    return [f"{RELEASE_NOTES}:{line}: {why} ({RULES})" for line, why in problems]


def version_mismatch() -> str | None:
    """Why a copy of backend/pyproject.toml's version differs from it, else None.

    The copies are frontend/package.json and the lock files backend/uv.lock and
    frontend/package-lock.json, one line for each copy that differs. The lines after them are
    the problems of docs/releases.md (release_notes_problems), such as a missing entry for the
    version (semver's spelling, as in package.json).
    """
    python = pyproject_version()
    try:
        expected = semver(python)
    except ValueError as error:
        return str(error)
    source = f"backend/pyproject.toml says {python}"
    in_semver = f"{source}, which is {expected} as semver spells it"
    try:
        top, root_package = package_lock_versions()
        copies = [
            ("frontend/package.json", package_json_version(), expected, in_semver),
            ("backend/uv.lock (package bwtidy)", uv_lock_version(), python, source),
            ("frontend/package-lock.json (version)", top, expected, in_semver),
            (
                'frontend/package-lock.json (packages[""].version)',
                root_package,
                expected,
                in_semver,
            ),
        ]
    except (OSError, KeyError, ValueError) as error:
        return f"cannot read a version: {error}"
    problems = [
        f"{where} says {actual} but {why}"
        for where, actual, wanted, why in copies
        if actual != wanted
    ]
    problems.extend(release_notes_problems(expected))
    return "\n".join(problems) or None

"""A release from docs/releases.md: prepare it, tag it, print its notes, check a pull request.

dev/release.md (To release) describes the flow: a pull request with `scripts/release prepare
VERSION`, then `scripts/release tag VERSION` on main, and .github/workflows/release.yml creates
the GitHub release from the version's entry and publishes the image. The pull request check
(`check-pr`) runs in .github/workflows/ci.yml.
"""

import datetime
import json
import posixpath
import re
import subprocess
from collections.abc import Callable
from pathlib import Path

from devlib import versions
from devlib.git import git
from devlib.paths import ROOT

NAME = "release"
NOTES = versions.RELEASE_NOTES
REPO_URL = versions.REPO_URL
## where docs.yml publishes the docs site, which serves docs/X.md at X/
SITE_URL = "https://tnek213.github.io/bwtidy/"
MAIN = "refs/remotes/origin/main"
NO_RELEASE_NOTE = "no release note"
## the paths whose change a user of the image sees, a prefix ending in / for a folder. Their
## tests (TESTS) and the Docker Hub overview, which the image does not hold, are left out.
USER_VISIBLE = ("backend/", "frontend/", "docker/", "Dockerfile", "compose.yaml", "example.env")
TESTS = re.compile(
    r"backend/tests/.*"
    r"|frontend/(.*/)?([^/]+\.test\.[jt]sx?|test-[^/]+\.[jt]sx?|testSupport\.[jt]sx?)"
    r"|docker/hub-overview\.md"
)
## the commands that write the version into the lock files, each in its folder (dev/release.md,
## The version)
LOCK_COMMANDS = (
    ("backend", ("uv", "lock")),
    ("demo", ("uv", "lock")),
    ("frontend", ("npm", "install", "--package-lock-only")),
)
LINK = re.compile(r"(\]\()([^()\s]+)(\))")
SCHEME = re.compile(r"[A-Za-z][A-Za-z0-9+.-]*:")
SECTION = re.compile(r"## (\S.*?)\s*")
SUBHEADING = re.compile(r"### (\S.*?)\s*")
BULLET_START = re.compile(r"[-*+][ \t]")
FENCE = re.compile(r"[ \t]*(```|~~~)")


def fail(message: str) -> SystemExit:
    return SystemExit(f"{NAME}: {message}")


def check_version(version: str) -> None:
    if not versions.VERSION.fullmatch(version):
        raise fail(
            f"{version!r} is no version as X.Y.Z, optionally -alpha.N, -beta.N or -rc.N "
            "(semver's spelling, as in frontend/package.json)"
        )


## --- the sections of docs/releases.md as lines


def split_sections(lines: list[str]) -> tuple[list[str], list[tuple[str, list[str]]]]:
    """The lines above the first ## heading and each ## section: its title and its lines, the
    heading first. A line in a fenced code block is never a heading."""
    preamble: list[str] = []
    found: list[tuple[str, list[str]]] = []
    fence = ""
    for line in lines:
        if fence:
            if line.strip().startswith(fence):
                fence = ""
        elif opened := FENCE.match(line):
            fence = opened.group(1)
        elif heading := SECTION.fullmatch(line):
            found.append((heading.group(1), [line]))
            continue
        (found[-1][1] if found else preamble).append(line)
    return preamble, found


def unreleased_lines(text: str) -> list[str]:
    """The lines of the section Unreleased in text, empty without one."""
    for title, lines in split_sections(text.splitlines())[1]:
        if title == versions.UNRELEASED:
            return lines
    return []


def _paragraphs(lines: list[str]) -> list[list[str]]:
    """Lines split at the blank lines, no blank line kept."""
    found: list[list[str]] = []
    current: list[str] = []
    for line in [*lines, ""]:
        if line.strip():
            current.append(line)
        elif current:
            found.append(current)
            current = []
    return found


def is_metadata(paragraph: list[str]) -> bool:
    """Whether a paragraph below a version heading is meant as its metadata line."""
    text = " ".join(line.strip() for line in paragraph)
    return bool(versions.DATE.match(text)) or "[GitHub release]" in text


class Entry:
    """The body of a version or of Unreleased: its paragraphs and its bullets by ### heading.

    The heading "" holds the bullets of a flat list. Each bullet keeps its lines as written.
    """

    def __init__(self, lines: list[str]) -> None:
        self.metadata: list[str] = []
        self.intro: list[list[str]] = []
        self.groups: dict[str, list[list[str]]] = {}
        heading = ""
        bullet: list[str] | None = None
        last: list[str] | None = None
        started = False
        for paragraph in _paragraphs(lines):
            if not started and not self.metadata and not self.intro and is_metadata(paragraph):
                self.metadata = paragraph
                continue
            for index, line in enumerate(paragraph):
                sub = SUBHEADING.fullmatch(line)
                if sub:
                    heading, bullet, last, started = sub.group(1), None, None, True
                    self.groups.setdefault(heading, [])
                elif BULLET_START.match(line):
                    bullet = last = [line]
                    started = True
                    self.groups.setdefault(heading, []).append(bullet)
                elif bullet is not None:
                    bullet.append(line)
                elif last is not None and index == 0 and line[:1].isspace():
                    ## a bullet goes on below a blank line when the line is indented under it
                    bullet = last
                    bullet += ["", line]
                elif not started:
                    if index == 0:
                        self.intro.append([])
                    self.intro[-1].append(line)
                else:
                    raise fail(f"{NOTES}: '{line.strip()}' is no bullet, run scripts/ci first")
            bullet = None

    def bullets(self) -> int:
        return sum(len(group) for group in self.groups.values())


def merged(old: Entry, new: Entry) -> dict[str, list[list[str]]]:
    """The bullets of old and then those of new, under the ### headings of the format.

    With a warning heading on either side, a flat list goes under ### Other changes.
    """
    warned = any(old.groups.get(name) or new.groups.get(name) for name in versions.WARNINGS)
    if not warned:
        return {"": [*old.groups.get("", []), *new.groups.get("", [])]}
    result = {
        name: [*old.groups.get(name, []), *new.groups.get(name, [])] for name in versions.WARNINGS
    }
    result[versions.OTHER_CHANGES] = [
        *old.groups.get(versions.OTHER_CHANGES, []),
        *old.groups.get("", []),
        *new.groups.get(versions.OTHER_CHANGES, []),
        *new.groups.get("", []),
    ]
    return result


def entry_lines(
    version: str, metadata: list[str], intro: list[list[str]], groups: dict[str, list[list[str]]]
) -> list[str]:
    lines = [f"## {version}", ""]
    for paragraph in [metadata, *intro]:
        lines += [*paragraph, ""]
    for heading, bullets in groups.items():
        if not bullets:
            continue
        if heading:
            lines += [f"### {heading}", ""]
        for bullet in bullets:
            lines += bullet
        lines.append("")
    return lines


def metadata_lines(version: str, date: datetime.date, previous: str | None) -> list[str]:
    """The metadata line of version, wrapped before the All changes link as text is."""
    lines = [f"{date.isoformat()} · [GitHub release]({REPO_URL}/releases/tag/v{version})"]
    if previous is not None:
        lines.append(f"· [All changes]({REPO_URL}/compare/v{previous}...v{version})")
    return lines


UNRELEASED_LINES = [
    f"## {versions.UNRELEASED}",
    "",
    versions.UNRELEASED_SENTENCE,
    "",
    versions.NOTHING_YET,
    "",
]


## --- prepare


def tag_exists(tag: str, root: Path) -> bool:
    found = git("rev-parse", "--quiet", "--verify", f"refs/tags/{tag}", repo=root, check=False)
    return found.returncode == 0


def refresh_locks(root: Path) -> None:
    """Write the version into the lock files with the commands of LOCK_COMMANDS."""
    for folder, command in LOCK_COMMANDS:
        print(f"{NAME}: {' '.join(command)} in {folder}/")
        result = subprocess.run(command, cwd=root / folder, check=False)
        if result.returncode != 0:
            raise fail(f"{' '.join(command)} in {folder}/ failed")


def bump(version: str, root: Path) -> None:
    """Write version into backend/pyproject.toml (Python's spelling) and frontend/package.json."""
    pyproject = root / "backend" / "pyproject.toml"
    text = pyproject.read_text()
    new, count = re.subn(
        r'(?ms)(^\[project\]$.*?^version = ")[^"]*(")',
        rf"\g<1>{versions.python_version(version)}\g<2>",
        text,
        count=1,
    )
    if count != 1:
        raise fail("backend/pyproject.toml has no version in [project]")
    pyproject.write_text(new)
    package = root / "frontend" / "package.json"
    text = package.read_text()
    ## the first "version" of the file is the package's own, the rest keeps its formatting
    new, count = re.subn(r'("version":\s*")[^"]*(")', rf"\g<1>{version}\g<2>", text, count=1)
    if count != 1 or json.loads(new)["version"] != version:
        raise fail("frontend/package.json has no version")
    package.write_text(new)


def prepare(
    version: str,
    today: datetime.date,
    root: Path = ROOT,
    refresh: Callable[[Path], None] = refresh_locks,
) -> list[str]:
    """Turn Unreleased into the entry of version, dated today, and bump the version to it.

    Returns the files it changed. A version that is the current one and has no tag yet keeps its
    entry: the bullets of Unreleased join it and its metadata line gets today's date.
    """
    check_version(version)
    current = json.loads((root / "frontend" / "package.json").read_text())["version"]
    path = root / NOTES
    preamble, found = split_sections(path.read_text().splitlines())
    titles = [title for title, _ in found]
    if versions.UNRELEASED not in titles:
        raise fail(f"{NOTES} has no ## {versions.UNRELEASED}, run scripts/ci first")
    unreleased = Entry(found[titles.index(versions.UNRELEASED)][1][1:])
    if unreleased.intro and unreleased.intro[0] == [versions.UNRELEASED_SENTENCE]:
        unreleased.intro = unreleased.intro[1:]
    if [paragraph for paragraph in unreleased.intro if paragraph != [versions.NOTHING_YET]]:
        raise fail(f"## {versions.UNRELEASED} holds more than bullets, run scripts/ci first")
    entries = [title for title in titles if versions.VERSION.fullmatch(title)]
    newest = entries[0] if entries else None
    gone = versions.withdrawn_versions(path.read_text())
    tag = f"v{version}"
    if tag_exists(tag, root):
        raise fail(f"the tag {tag} exists, {version} is released already")

    if version == current and version in entries:
        if version != newest:
            raise fail(f"## {version} is not the newest entry of {NOTES}")
        index = titles.index(version)
        old = Entry(found[index][1][1:])
        older = [title for title in entries[1:] if title not in gone]
        previous = older[0] if older else None
        groups = merged(old, unreleased)
        found[index] = (
            version,
            entry_lines(version, metadata_lines(version, today, previous), old.intro, groups),
        )
        changed = [NOTES]
    else:
        if version in entries:
            raise fail(f"{NOTES} has an entry for {version} already, but the version is {current}")
        if newest is not None and versions.version_key(version) <= versions.version_key(newest):
            raise fail(f"{version} is not newer than {newest}, the newest entry of {NOTES}")
        if newest is not None and newest not in gone and not tag_exists(f"v{newest}", root):
            raise fail(
                f"{newest} has no tag yet, release it first with scripts/release tag {newest} "
                f"or prepare it again with scripts/release prepare {newest}"
            )
        if not unreleased.bullets():
            raise fail(
                f"## {versions.UNRELEASED} says '{versions.NOTHING_YET}', so {version} would have "
                "no changes to name"
            )
        released = [title for title in entries if title not in gone]
        previous = released[0] if released else None
        lines = entry_lines(
            version, metadata_lines(version, today, previous), [], merged(Entry([]), unreleased)
        )
        found.insert(titles.index(versions.UNRELEASED) + 1, (version, lines))
        bump(version, root)
        refresh(root)
        changed = [
            "backend/pyproject.toml",
            "backend/uv.lock",
            "demo/uv.lock",
            "frontend/package.json",
            "frontend/package-lock.json",
            NOTES,
        ]
    found[titles.index(versions.UNRELEASED)] = (versions.UNRELEASED, UNRELEASED_LINES)
    text = "\n".join(preamble + [line for _, lines in found for line in lines]).rstrip() + "\n"
    path.write_text(text)
    return changed


## --- tag


def show(ref: str, path: str, root: Path) -> str | None:
    """The file path as ref has it, None when ref has no such file."""
    result = git("show", f"{ref}:{path}", repo=root, check=False)
    return result.stdout if result.returncode == 0 else None


def commit_date(ref: str, root: Path) -> str:
    """The date (YYYY-MM-DD, Swedish time) of the commit ref points at."""
    shown = git("log", "-1", "--format=%ct", f"{ref}^{{commit}}", repo=root, check=False)
    if shown.returncode != 0 or not shown.stdout.strip().isdigit():
        raise fail(f"git cannot read the commit of {ref}: {shown.stderr.strip()}")
    return versions.day_of(int(shown.stdout))


def release_problems(version: str, ref: str, root: Path = ROOT) -> list[str]:
    """Why the commit ref cannot be released as version, empty when it can.

    Its frontend/package.json has version, and the entry of version in its docs/releases.md has
    a metadata line dated the date (Swedish time) of the commit, as the tag vVERSION on it will
    have.
    """
    package = show(ref, "frontend/package.json", root)
    if package is None:
        return [f"{ref} has no frontend/package.json"]
    actual = json.loads(package).get("version")
    if actual != version:
        return [f"the version of {ref} is {actual}, not {version}"]
    notes = show(ref, NOTES, root) or ""
    section = next((s for s in versions.sections(notes) if s.title == version), None)
    if section is None:
        return [f"{NOTES} of {ref} has no ## {version}"]
    date = versions.metadata_date(section)
    again = (
        f"run scripts/release prepare {version} again in a new pull request, which writes "
        "today's date"
    )
    if date is None:
        return [f"## {version} in {NOTES} of {ref} has no metadata line with its date, {again}"]
    committed = commit_date(ref, root)
    if date != committed:
        return [
            f"## {version} in {NOTES} of {ref} is dated {date}, but its commit is from {committed} "
            f"(Swedish time) and the tag must have the date of its entry: {again}"
        ]
    return []


def fetch(root: Path) -> None:
    result = git("fetch", "--tags", "origin", repo=root, check=False)
    if result.returncode != 0:
        raise fail(f"git fetch origin failed: {result.stderr.strip()}")


def push_tag(tag: str, root: Path) -> None:
    result = git("push", "origin", f"refs/tags/{tag}", repo=root, check=False)
    if result.returncode != 0:
        git("tag", "--delete", tag, repo=root, check=False)
        raise fail(f"git push origin {tag} failed: {result.stderr.strip()}")


def tag(
    version: str,
    root: Path = ROOT,
    fetch: Callable[[Path], None] = fetch,
    push: Callable[[str, Path], None] = push_tag,
) -> str:
    """Tag origin/main's head as vVERSION (annotated) and push the tag, returns the commit."""
    check_version(version)
    fetch(root)
    name = f"v{version}"
    if tag_exists(name, root):
        raise fail(f"the tag {name} exists already")
    problems = release_problems(version, MAIN, root)
    if problems:
        raise fail("\n".join(problems))
    commit = git("rev-parse", f"{MAIN}^{{commit}}", repo=root).stdout.strip()
    git("tag", "--annotate", "--message", f"bwtidy {version}", name, commit, repo=root)
    push(name, root)
    return commit


## --- notes


def absolute(target: str) -> str:
    """A link of docs/releases.md as an absolute URL, the page's address on the docs site for a
    page. A URL or an address stays as it is."""
    if SCHEME.match(target) or target.startswith("/"):
        return target
    path, hash_mark, anchor = target.partition("#")
    if not path:
        path = posixpath.basename(NOTES)
    path = posixpath.normpath(posixpath.join(posixpath.dirname(NOTES), path))
    path = posixpath.relpath(path, "docs")
    if path.endswith(".md"):
        page = path.removesuffix(".md")
        if page == "index":
            path = ""
        elif page.endswith("/index"):
            path = page.removesuffix("index")
        else:
            path = f"{page}/"
    return f"{SITE_URL}{path}{hash_mark}{anchor}"


def unwrap(text: str) -> str:
    """text with each paragraph and each bullet on one line, code blocks as they are.

    GitHub shows every line break of a release body, so the wrapping at 80 characters of
    docs/releases.md would break its lines in the middle.
    """
    lines: list[str] = []
    fenced = False
    for line in text.splitlines():
        if FENCE.match(line):
            fenced = not fenced
            lines.append(line)
            continue
        joins = bool(
            not fenced
            and line.strip()
            and lines
            and lines[-1].strip()
            and not lines[-1].lstrip().startswith("#")
            and not FENCE.match(lines[-1])
            and not line.lstrip().startswith("#")
            and not BULLET_START.match(line.lstrip())
        )
        if joins:
            lines[-1] = f"{lines[-1].rstrip()} {line.strip()}"
        else:
            lines.append(line)
    return "\n".join(lines)


def notes(version: str, root: Path = ROOT) -> str:
    """The body of the GitHub release of version: its entry without its heading and metadata
    line, every paragraph and bullet on one line and every link absolute."""
    check_version(version)
    for title, lines in split_sections((root / NOTES).read_text().splitlines())[1]:
        if title != version:
            continue
        body = lines[1:]
        paragraphs = _paragraphs(body)
        if paragraphs and is_metadata(paragraphs[0]):
            first = paragraphs[0]
            start = body.index(first[0])
            body = body[start + len(first) :]
        text = unwrap("\n".join(body).strip("\n"))
        return LINK.sub(lambda m: m.group(1) + absolute(m.group(2)) + m.group(3), text) + "\n"
    raise fail(f"{NOTES} has no ## {version}")


## --- the check of a pull request


def user_visible(path: str) -> bool:
    if TESTS.fullmatch(path):
        return False
    return any(
        path == name or (name.endswith("/") and path.startswith(name)) for name in USER_VISIBLE
    )


def check_pr(base: str, head: str, labels: list[str], root: Path = ROOT) -> str | None:
    """Why the pull request from base to head lacks its release note, None when it has one.

    A pull request that changes a path of USER_VISIBLE changes the section Unreleased of
    docs/releases.md too, or has the label NO_RELEASE_NOTE.
    """
    if NO_RELEASE_NOTE in labels:
        return None
    forked = git("merge-base", base, head, repo=root, check=False)
    if forked.returncode != 0:
        raise fail(f"git merge-base {base} {head} failed: {forked.stderr.strip()}")
    start = forked.stdout.strip()
    diff = git("diff", "--name-only", "--no-renames", "-z", start, head, repo=root, check=False)
    if diff.returncode != 0:
        raise fail(f"git diff {start} {head} failed: {diff.stderr.strip()}")
    visible = [path for path in diff.stdout.split("\0") if path and user_visible(path)]
    if not visible:
        return None
    before = unreleased_lines(show(start, NOTES, root) or "")
    after = unreleased_lines(show(head, NOTES, root) or "")
    if before != after:
        return None
    shown = ", ".join(visible[:5]) + (f" and {len(visible) - 5} more" if len(visible) > 5 else "")
    return (
        f"This pull request changes what a user sees ({shown}) but not ## {versions.UNRELEASED} "
        f"in {NOTES}. Add its bullet there as dev/release.md (The release notes) says, or give "
        f"the pull request the label '{NO_RELEASE_NOTE}' when no user sees the change."
    )


## --- the command line


def main(args: list[str], usage: str) -> int:
    if not args or args[0] in ("-h", "--help"):
        print(usage, end="")
        return 0 if args else 2
    command, rest = args[0], args[1:]
    if command == "check-pr" and len(rest) == 3:
        base, head, labels = rest
        names = json.loads(labels or "[]")
        if not isinstance(names, list) or not all(isinstance(name, str) for name in names):
            raise fail(f"the labels are a JSON list of names, not {labels!r}")
        problem = check_pr(base, head, names)
        if problem:
            print(f"{NAME}: {problem}")
            return 1
        print(f"{NAME}: the pull request has its release note or needs none")
        return 0
    if command == "lint" and not rest:
        problem = versions.version_mismatch()
        if problem:
            print(problem)
            return 1
        return 0
    if len(rest) != 1:
        print(usage, end="")
        return 2
    version = rest[0]
    if command == "prepare":
        today = versions.today()
        changed = prepare(version, today)
        problem = versions.version_mismatch()
        if problem:
            print(problem)
            return 1
        print(f"{NAME}: {version} is prepared, dated {today.isoformat()} (Swedish time). Commit")
        for path in changed:
            print(f"  {path}")
        print(
            f"as 'Release bwtidy {version}' in a pull request and merge it today (Swedish time), "
            f"then run scripts/release tag {version}"
        )
        return 0
    if command == "tag":
        commit = tag(version)
        print(f"{NAME}: tagged {commit[:7]} as v{version} and pushed the tag")
        return 0
    if command == "check-tag":
        check_version(version)
        problems = release_problems(version, f"refs/tags/v{version}")
        if problems:
            print("\n".join(f"{NAME}: {problem}" for problem in problems))
            return 1
        return 0
    if command == "notes":
        print(notes(version), end="")
        return 0
    print(usage, end="")
    return 2

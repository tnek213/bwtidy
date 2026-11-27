"""scripts/devlib/release.py: prepare, tag and the notes of a release, and the check of a pull
request's release note (dev/release.md, To release)."""

import datetime
import json
import tomllib

import pytest
from devlib import release, versions

from tests.support.git_repo import git
from tests.support.release_notes import (
    INTRO,
    PYTHON,
    REPO,
    SEMVER,
    TAGGED,
    TAGS,
    UNRELEASED,
    FakeRepo,
    metadata,
    pull,
)

NEW = "0.1.0-alpha.2"
TODAY = datetime.date(2026, 3, 9)
SENTENCE = "Changes on main that are not yet in a versioned Docker image."
SITE = "https://tnek213.github.io/bwtidy/"


@pytest.fixture
def repo(tmp_path, monkeypatch) -> FakeRepo:
    ## semver loads bwtidy/version.py from ROOT once and keeps it, so load it before ROOT moves
    versions.semver(PYTHON)
    monkeypatch.setattr(versions, "ROOT", tmp_path)
    return FakeRepo(tmp_path)


def wrapped(version: str, date: str, previous: str | None) -> str:
    """The metadata line as prepare writes it, wrapped before the All changes link."""
    return metadata(version, date, previous).replace(" · [All changes]", "\n· [All changes]")


def notes_of(repo: FakeRepo) -> str:
    return (repo.root / "docs" / "releases.md").read_text()


def no_refresh(root) -> None:
    raise AssertionError("prepare refreshed the lock files of a version it did not bump")


def unreleased(*bullets: str) -> str:
    return f"## Unreleased\n\n{SENTENCE}\n\n" + "".join(f"{b}\n" for b in bullets) + "\n"


## --- prepare


def test_prepare_turns_unreleased_into_the_new_version_and_bumps_it(repo):
    bullets = (
        f"- Sort the grid by any column {pull(12)}\n"
        f"- Find a long line that wraps\n  onto the next one {pull(13)}"
    )
    repo.tagged(TAGGED.replace("Nothing yet.", bullets))
    refreshed = []

    def refresh(root):
        ## what uv lock and npm install --package-lock-only write
        refreshed.append(root)
        repo.uv_lock("0.1.0a2")
        repo.package_lock(NEW, NEW)

    changed = release.prepare(NEW, TODAY, repo.root, refresh)

    rest = TAGGED[TAGGED.index(f"## {SEMVER}") :]
    assert notes_of(repo) == (
        INTRO
        + UNRELEASED
        + f"## {NEW}\n\n{wrapped(NEW, '2026-03-09', SEMVER)}\n\n{bullets}\n\n"
        + rest
    )
    pyproject = tomllib.loads((repo.root / "backend" / "pyproject.toml").read_text())
    assert pyproject["project"]["version"] == "0.1.0a2"
    package = json.loads((repo.root / "frontend" / "package.json").read_text())
    assert package["version"] == NEW
    assert refreshed == [repo.root]
    assert "docs/releases.md" in changed
    assert "backend/pyproject.toml" in changed
    assert versions.version_mismatch() is None


def test_the_lock_files_are_refreshed_as_dev_release_md_says():
    assert release.LOCK_COMMANDS == (
        ("backend", ("uv", "lock")),
        ("demo", ("uv", "lock")),
        ("frontend", ("npm", "install", "--package-lock-only")),
    )


def test_prepare_refuses_a_new_version_without_changes(repo):
    repo.tagged()
    before = notes_of(repo)
    with pytest.raises(SystemExit, match="Nothing yet."):
        release.prepare(NEW, TODAY, repo.root, no_refresh)
    assert notes_of(repo) == before
    assert json.loads((repo.root / "frontend" / "package.json").read_text())["version"] == SEMVER


@pytest.mark.parametrize(
    ("version", "says"),
    [
        ("0.1.0a2", "no version as X.Y.Z"),
        ("0.0.3", "not newer than 0.1.0-alpha.1"),
        ("0.0.2", "the tag v0.0.2 exists"),
        (SEMVER, f"the tag v{SEMVER} exists"),
    ],
)
def test_prepare_refuses_a_version_it_cannot_prepare(repo, version, says):
    repo.tagged(TAGGED.replace("Nothing yet.", f"- Sort the grid {pull(12)}"))
    before = notes_of(repo)
    with pytest.raises(SystemExit, match=says):
        release.prepare(version, TODAY, repo.root, no_refresh)
    assert notes_of(repo) == before


def test_prepare_refuses_a_new_version_while_the_newest_has_no_tag(repo):
    repo.release_notes(
        INTRO + unreleased(f"- Sort the grid {pull(12)}") + f"## {SEMVER}\n\n- Start {pull(1)}\n"
    )
    with pytest.raises(SystemExit, match=f"{SEMVER} has no tag yet"):
        release.prepare(NEW, TODAY, repo.root, no_refresh)


OLDER = f"## 0.0.2\n\n{metadata('0.0.2', '2026-02-03', None)}\n\n- Begin {pull(1)}\n"


def test_prepare_fills_the_entry_of_the_bumped_version_without_a_tag(repo):
    repo.tag("0.0.2", TAGS["0.0.2"])
    repo.release_notes(
        INTRO
        + unreleased(f"- Sort the grid {pull(12)}")
        + f"## {SEMVER}\n\nThe first alpha.\n\n- Start bwtidy {pull(2)}\n\n"
        + OLDER
    )
    release.prepare(SEMVER, TODAY, repo.root, no_refresh)
    assert notes_of(repo) == (
        INTRO
        + UNRELEASED
        + f"## {SEMVER}\n\n{wrapped(SEMVER, '2026-03-09', '0.0.2')}\n\n"
        + f"The first alpha.\n\n- Start bwtidy {pull(2)}\n- Sort the grid {pull(12)}\n\n"
        + OLDER
    )
    assert tomllib.loads((repo.root / "backend" / "pyproject.toml").read_text())["project"][
        "version"
    ] == (PYTHON)
    assert versions.version_mismatch() is None


def test_prepare_again_writes_todays_date_over_the_older_one(repo):
    repo.tag("0.0.2", TAGS["0.0.2"])
    entry = f"- Start bwtidy {pull(2)}\n\n"
    repo.release_notes(
        INTRO
        + UNRELEASED
        + f"## {SEMVER}\n\n{wrapped(SEMVER, '2026-03-01', '0.0.2')}\n\n"
        + entry
        + OLDER
    )
    release.prepare(SEMVER, TODAY, repo.root, no_refresh)
    assert notes_of(repo) == (
        INTRO + UNRELEASED + f"## {SEMVER}\n\n{wrapped(SEMVER, '2026-03-09', '0.0.2')}\n\n"
        f"{entry}{OLDER}"
    )


def test_prepare_puts_the_bullets_under_the_headings_of_the_format(repo):
    repo.tag("0.0.2", TAGS["0.0.2"])
    repo.release_notes(
        INTRO
        + f"## Unreleased\n\n{SENTENCE}\n\n### Security\n\n- Update now {pull(14)}\n\n"
        + f"### Other changes\n\n- Sort the grid {pull(12)}\n\n"
        + f"## {SEMVER}\n\n### Breaking changes\n\n- Set the key {pull(3)}\n\n"
        + f"### Other changes\n\n- Start bwtidy {pull(2)}\n\n"
        + OLDER
    )
    release.prepare(SEMVER, TODAY, repo.root, no_refresh)
    assert notes_of(repo) == (
        INTRO
        + UNRELEASED
        + f"## {SEMVER}\n\n{wrapped(SEMVER, '2026-03-09', '0.0.2')}\n\n"
        + f"### Breaking changes\n\n- Set the key {pull(3)}\n\n"
        + f"### Security\n\n- Update now {pull(14)}\n\n"
        + f"### Other changes\n\n- Start bwtidy {pull(2)}\n- Sort the grid {pull(12)}\n\n"
        + OLDER
    )
    assert versions.version_mismatch() is None


## --- tag


def on_origin_main(repo: FakeRepo, date: str, committed: str) -> str:
    """The notes with the entry of SEMVER dated date, committed at committed as origin/main."""
    repo.release_notes(
        INTRO + UNRELEASED + f"## {SEMVER}\n\n{metadata(SEMVER, date, None)}\n\n- Start {pull(1)}\n"
    )
    sha = repo.commit(committed)
    git(repo.root, "update-ref", release.MAIN, sha)
    return sha


class Remote:
    """The fetch and the push of tag, which the tests never send anywhere."""

    def __init__(self) -> None:
        self.fetched: list[object] = []
        self.pushed: list[str] = []

    def fetch(self, root) -> None:
        self.fetched.append(root)

    def push(self, tag, root) -> None:
        self.pushed.append(tag)


def tag(repo: FakeRepo, version: str = SEMVER) -> tuple[Remote, str]:
    remote = Remote()
    return remote, release.tag(version, repo.root, remote.fetch, remote.push)


def test_tag_tags_the_head_of_origin_main_and_pushes_it(repo):
    ## 23:30 in New York on 2026-03-04 is 2026-03-05 in UTC
    sha = on_origin_main(repo, "2026-03-05", "2026-03-04T23:30:00-05:00")
    ## a local commit that is not on origin/main yet is not the release
    repo.commit("2026-03-05T12:00:00+00:00", "Change more")
    remote, commit = tag(repo)
    assert commit == sha
    assert remote.fetched == [repo.root]
    assert remote.pushed == [f"v{SEMVER}"]
    assert git(repo.root, "cat-file", "-t", f"v{SEMVER}") == "tag"
    assert git(repo.root, "rev-parse", f"v{SEMVER}^{{commit}}") == sha
    assert release.release_problems(SEMVER, f"refs/tags/v{SEMVER}", repo.root) == []


def test_tag_refuses_a_date_that_is_not_the_date_of_the_commit(repo):
    on_origin_main(repo, "2026-03-05", "2026-03-06T00:30:00+00:00")
    with pytest.raises(SystemExit) as raised:
        tag(repo)
    message = str(raised.value)
    assert "dated 2026-03-05, but its commit is from 2026-03-06" in message
    assert f"run scripts/release prepare {SEMVER} again in a new pull request" in message
    assert git(repo.root, "tag", "--list") == ""


def test_tag_refuses_an_entry_without_its_date(repo):
    repo.release_notes(INTRO + UNRELEASED + f"## {SEMVER}\n\n- Start {pull(1)}\n")
    git(repo.root, "update-ref", release.MAIN, repo.commit("2026-03-05T12:00:00+00:00"))
    with pytest.raises(SystemExit, match="has no metadata line"):
        tag(repo)


def test_tag_refuses_a_tag_that_exists(repo):
    on_origin_main(repo, "2026-03-05", "2026-03-05T12:00:00+00:00")
    git(repo.root, "tag", "-a", "-m", "x", f"v{SEMVER}")
    remote = Remote()
    with pytest.raises(SystemExit, match=f"the tag v{SEMVER} exists"):
        release.tag(SEMVER, repo.root, remote.fetch, remote.push)
    assert remote.pushed == []


def test_tag_refuses_another_version_than_origin_mains(repo):
    on_origin_main(repo, "2026-03-05", "2026-03-05T12:00:00+00:00")
    remote = Remote()
    with pytest.raises(SystemExit, match=f"the version of {release.MAIN} is {SEMVER}, not {NEW}"):
        release.tag(NEW, repo.root, remote.fetch, remote.push)
    assert remote.pushed == []
    assert git(repo.root, "tag", "--list") == ""


## --- notes


def test_notes_are_the_entry_without_heading_and_metadata_with_absolute_links(repo):
    repo.release_notes(
        INTRO
        + UNRELEASED
        + f"## {NEW}\n\n{wrapped(NEW, '2026-03-09', SEMVER)}\n\n"
        + "Read [Road to\n1.0](explanation/road-to-1.0.md) first.\n\n"
        + f"- Turn on [usage stats](reference/configuration.md#usage-stats) {pull(4)}\n"
        + f"- Start at [the home page](index.md) or [here](#unreleased) {pull(5)}\n"
        + f"- See [the code]({REPO}/blob/main/README.md) and ![a grid](images/grid.png)"
        + f" {pull(6)}\n\n"
        + f"## {SEMVER}\n\n- Start {pull(1)}\n"
    )
    assert release.notes(NEW, repo.root) == (
        f"Read [Road to\n1.0]({SITE}explanation/road-to-1.0/) first.\n\n"
        f"- Turn on [usage stats]({SITE}reference/configuration/#usage-stats) {pull(4)}\n"
        f"- Start at [the home page]({SITE}) or [here]({SITE}releases/#unreleased) {pull(5)}\n"
        f"- See [the code]({REPO}/blob/main/README.md) and ![a grid]({SITE}images/grid.png)"
        f" {pull(6)}\n"
    )
    assert release.notes(SEMVER, repo.root) == f"- Start {pull(1)}\n"


def test_notes_of_a_version_without_an_entry_fail(repo):
    with pytest.raises(SystemExit, match="has no ## 0.0.9"):
        release.notes("0.0.9", repo.root)


def test_notes_of_the_repo_have_no_relative_link():
    text = release.notes("1.0.0-beta.1")
    assert not text.startswith("1.0.0-beta.1")
    assert "[GitHub release]" not in text
    targets = [match.group(2) for match in release.LINK.finditer(text)]
    assert targets
    assert all(target.startswith("https://") for target in targets)


## --- check-pr


def pull_request(repo: FakeRepo, files: dict[str, str], notes: str | None = None) -> str | None:
    """check_pr for a branch off main that writes files (and the notes, when given)."""
    repo.tagged()
    base = repo.commit("2026-03-06T12:00:00+00:00")
    git(repo.root, "checkout", "--quiet", "-b", "feature")
    for name, text in files.items():
        path = repo.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    if notes is not None:
        repo.release_notes(notes)
    head = repo.commit("2026-03-07T12:00:00+00:00")
    return release.check_pr(base, head, [], repo.root)


WITH_BULLET = TAGGED.replace("Nothing yet.", f"- Sort the grid {pull(12)}")


def test_a_change_a_user_sees_without_its_note_fails(repo):
    problem = pull_request(repo, {"backend/src/bwtidy/app.py": "x = 1\n"})
    assert problem is not None
    assert "backend/src/bwtidy/app.py" in problem
    assert "## Unreleased in docs/releases.md" in problem
    assert "dev/release.md (The release notes)" in problem
    assert "'no release note'" in problem


@pytest.mark.parametrize(
    "name", ["Dockerfile", "compose.yaml", "example.env", "docker/x.js", "frontend/src/App.tsx"]
)
def test_every_path_a_user_sees_needs_a_note(repo, name):
    assert pull_request(repo, {name: "x\n"}) is not None


def test_a_change_a_user_sees_with_its_note_passes(repo):
    assert pull_request(repo, {"backend/src/bwtidy/app.py": "x = 1\n"}, WITH_BULLET) is None


@pytest.mark.parametrize(
    "name",
    [
        "backend/tests/unit/test_app.py",
        "frontend/src/App.test.tsx",
        "frontend/src/grid/Grid.test.ts",
        "frontend/src/test-utils.tsx",
        "frontend/src/grid/testSupport.tsx",
        "docker/hub-overview.md",
        "docs/how-to/run.md",
        "dev/release.md",
    ],
)
def test_a_change_no_user_sees_passes(repo, name):
    assert pull_request(repo, {name: "x\n"}) is None


def test_the_label_passes_a_change_without_a_note(repo):
    repo.tagged()
    base = repo.commit("2026-03-06T12:00:00+00:00")
    (repo.root / "Dockerfile").write_text("FROM x\n")
    head = repo.commit("2026-03-07T12:00:00+00:00")
    assert release.check_pr(base, head, [], repo.root) is not None
    assert release.check_pr(base, head, ["dependencies", "no release note"], repo.root) is None


def test_a_note_main_got_meanwhile_is_not_the_pull_requests(repo):
    repo.tagged()
    forked = repo.commit("2026-03-06T12:00:00+00:00")
    git(repo.root, "checkout", "--quiet", "-b", "feature")
    (repo.root / "Dockerfile").write_text("FROM x\n")
    head = repo.commit("2026-03-07T12:00:00+00:00")
    git(repo.root, "checkout", "--quiet", "main")
    repo.release_notes(WITH_BULLET)
    base = repo.commit("2026-03-08T12:00:00+00:00")
    assert base != forked
    assert release.check_pr(base, head, [], repo.root) is not None


def test_every_path_a_user_sees_is_in_the_repo():
    for name in release.USER_VISIBLE:
        assert (versions.ROOT / name).exists(), name


def test_the_labels_are_a_json_list_of_names():
    with pytest.raises(SystemExit, match="JSON list of names"):
        release.main(["check-pr", "a", "b", '{"name": "x"}'], "")

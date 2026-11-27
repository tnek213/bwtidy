import pytest
from devlib import health, versions

from tests.support.release_notes import (
    DRIFTED,
    INTRO,
    PYTHON,
    REPO,
    RULES,
    SEMVER,
    TAGGED,
    TAGS,
    UNRELEASED,
    FakeRepo,
    metadata,
    pull,
    withdrawn,
)


@pytest.fixture
def repo(tmp_path, monkeypatch) -> FakeRepo:
    ## semver loads bwtidy/version.py from ROOT once and keeps it, so load it before ROOT moves
    versions.semver(PYTHON)
    monkeypatch.setattr(versions, "ROOT", tmp_path)
    return FakeRepo(tmp_path)


def test_the_versions_of_the_repo_agree():
    assert versions.version_mismatch() is None


def test_the_fake_repo_agrees(repo):
    assert versions.version_mismatch() is None


def test_ci_names_a_package_json_that_drifted(repo):
    repo.package_json(DRIFTED)
    assert versions.version_mismatch() == (
        f"frontend/package.json says {DRIFTED} but backend/pyproject.toml says {PYTHON}, "
        f"which is {SEMVER} as semver spells it"
    )


def test_ci_names_a_uv_lock_that_drifted(repo):
    repo.uv_lock("0.1.0a2")
    assert versions.version_mismatch() == (
        f"backend/uv.lock (package bwtidy) says 0.1.0a2 but backend/pyproject.toml says {PYTHON}"
    )


def test_ci_names_the_top_level_version_of_a_package_lock_that_drifted(repo):
    repo.package_lock(DRIFTED, SEMVER)
    assert versions.version_mismatch() == (
        f"frontend/package-lock.json (version) says {DRIFTED} but backend/pyproject.toml says "
        f"{PYTHON}, which is {SEMVER} as semver spells it"
    )


def test_ci_names_the_root_package_of_a_package_lock_that_drifted(repo):
    repo.package_lock(SEMVER, DRIFTED)
    assert versions.version_mismatch() == (
        f'frontend/package-lock.json (packages[""].version) says {DRIFTED} but '
        f"backend/pyproject.toml says {PYTHON}, which is {SEMVER} as semver spells it"
    )


def test_ci_names_every_copy_that_drifted(repo):
    repo.package_json(DRIFTED)
    repo.uv_lock("0.1.0a2")
    mismatch = versions.version_mismatch()
    assert mismatch is not None
    assert [line.split(" says ")[0] for line in mismatch.splitlines()] == [
        "frontend/package.json",
        "backend/uv.lock (package bwtidy)",
    ]


def test_ci_names_a_uv_lock_without_bwtidy(repo):
    (repo.root / "backend" / "uv.lock").write_text('version = 1\n\n[[package]]\nname = "anyio"\n')
    mismatch = versions.version_mismatch()
    assert mismatch is not None
    assert "backend/uv.lock has no package bwtidy" in mismatch


def problems(repo: FakeRepo, text: str) -> list[str]:
    repo.release_notes(text)
    return versions.release_notes_problems(SEMVER)


def line_of(text: str, needle: str, after: int = 0) -> int:
    """The number of the first line (from 1) below line after that holds needle."""
    for number, line in enumerate(text.splitlines(), start=1):
        if number > after and needle in line:
            return number
    raise AssertionError(f"{needle!r} not in the text")


def only(found: list[str], line: int, says: str) -> None:
    """found is the one problem on line, which says says and points to the rules."""
    assert len(found) == 1, found
    assert found[0].startswith(f"docs/releases.md:{line}: "), found
    assert says in found[0]
    assert found[0].endswith(RULES)


def test_tagged_notes_with_every_heading_pass(repo):
    repo.tagged()
    assert versions.release_notes_problems(SEMVER) == []


def test_the_date_is_the_utc_date_of_the_tagged_commit(repo):
    repo.tagged(TAGGED.replace("2026-03-05", "2026-03-04"))
    found = versions.release_notes_problems(SEMVER)
    only(found, line_of(TAGGED, "2026-03-05"), "is not 2026-03-05, the UTC date of the commit")


def test_a_date_that_differs_from_the_tag_fails(repo):
    text = TAGGED.replace("2026-02-03 ·", "2026-02-04 ·")
    repo.tagged(text)
    found = versions.release_notes_problems(SEMVER)
    only(found, line_of(text, "2026-02-04"), "the date 2026-02-04 is not 2026-02-03")


def test_a_wrong_h1_fails(repo):
    text = INTRO.replace("# Release notes", "# Changelog") + UNRELEASED + f"## {SEMVER}\n"
    only(problems(repo, text), 1, "starts with the heading '# Release notes'")


def test_unreleased_missing_fails(repo):
    text = INTRO + f"## {SEMVER}\n\n- Start bwtidy {pull(1)}\n"
    only(problems(repo, text), line_of(text, f"## {SEMVER}"), "no ## Unreleased")


def test_unreleased_below_a_version_fails(repo):
    text = INTRO + f"## {SEMVER}\n\n- Start bwtidy {pull(1)}\n\n" + UNRELEASED
    only(problems(repo, text), line_of(text, "## Unreleased"), "is the first ## heading")


def test_unreleased_without_its_sentence_fails(repo):
    text = INTRO + "## Unreleased\n\nNothing yet.\n\n" + f"## {SEMVER}\n"
    only(problems(repo, text), line_of(text, "Nothing yet."), "starts with the line")


def test_unreleased_with_bullets_passes(repo):
    text = TAGGED.replace("Nothing yet.", f"- Sort the grid by any column {pull(12)}")
    repo.tagged(text)
    assert versions.release_notes_problems(SEMVER) == []


def test_unreleased_with_a_warning_heading_passes(repo):
    text = TAGGED.replace(
        "Nothing yet.",
        f"### Breaking changes\n\n- Set the new key {pull(12)}\n\n"
        f"### Other changes\n\n- Sort the grid {pull(13)}",
    )
    repo.tagged(text)
    assert versions.release_notes_problems(SEMVER) == []


def test_nothing_yet_with_bullets_fails(repo):
    text = TAGGED.replace("Nothing yet.", f"Nothing yet.\n\n- Sort the grid {pull(12)}")
    repo.tagged(text)
    found = versions.release_notes_problems(SEMVER)
    only(found, line_of(text, "- Sort the grid"), "'Nothing yet.' stands alone")


def test_unreleased_without_bullets_or_nothing_yet_fails(repo):
    text = TAGGED.replace("Nothing yet.\n\n", "")
    repo.tagged(text)
    found = versions.release_notes_problems(SEMVER)
    only(found, line_of(text, "## Unreleased"), "has bullets or the line 'Nothing yet.'")


def test_a_paragraph_under_unreleased_fails(repo):
    text = TAGGED.replace("Nothing yet.", f"More to come.\n\n- Sort the grid {pull(12)}")
    repo.tagged(text)
    found = versions.release_notes_problems(SEMVER)
    only(found, line_of(text, "More to come."), "holds only bullets")


def test_a_dated_version_heading_fails(repo):
    text = TAGGED.replace("## 0.0.2\n", "## 0.0.2 (2026-01-01)\n")
    repo.tagged(text)
    line = line_of(text, "## 0.0.2 (")
    found = [p for p in versions.release_notes_problems(SEMVER) if f":{line}: " in p]
    assert found == [
        f"docs/releases.md:{line}: a version heading holds the version alone: ## 0.0.2 {RULES}"
    ]


def test_a_heading_that_is_no_version_fails(repo):
    text = INTRO + UNRELEASED + f"## Road to 1.0\n\nThe plan.\n\n## {SEMVER}\n\n- A {pull(1)}\n"
    only(problems(repo, text), line_of(text, "## Road to 1.0"), "## Road to 1.0 is no version")


def test_versions_out_of_order_fail(repo):
    text = (
        INTRO
        + UNRELEASED
        + f"## 0.0.2\n\n- B {pull(2)}\n\n"
        + f"## {SEMVER}\n\n- A {pull(1)}\n\n"
        + f"## 0.0.1\n\n- C {pull(3)}\n"
    )
    found = [p for p in problems(repo, text) if "newer than" in p]
    only(found, line_of(text, f"## {SEMVER}"), f"## {SEMVER} is newer than ## 0.0.2 above it")


def test_a_pre_release_is_older_than_its_release(repo):
    text = INTRO + UNRELEASED + f"## 0.1.0\n\n- B {pull(2)}\n\n## {SEMVER}\n\n- A {pull(1)}\n"
    assert not [p for p in problems(repo, text) if "newer than" in p]
    text = (
        INTRO + UNRELEASED + f"## {SEMVER}\n\n- A {pull(1)}\n\n## 0.1.0-beta.1\n\n- B {pull(2)}\n"
    )
    found = [p for p in problems(repo, text) if "newer than" in p]
    only(found, line_of(text, "## 0.1.0-beta.1"), "newer than")


def test_a_version_twice_fails(repo):
    text = INTRO + UNRELEASED + f"## {SEMVER}\n\n- A {pull(1)}\n\n## {SEMVER}\n\n- B {pull(2)}\n"
    found = [p for p in problems(repo, text) if "twice" in p]
    first = line_of(text, f"## {SEMVER}")
    only(found, line_of(text, f"## {SEMVER}", after=first), f"first on line {first}")


def test_the_current_version_missing_fails(repo):
    text = INTRO + UNRELEASED + f"## 0.0.9\n\n- Older {pull(1)}\n"
    only(problems(repo, text), line_of(text, "## 0.0.9"), f"no ## {SEMVER}, the current version")


def test_a_longer_version_is_not_the_version(repo):
    text = INTRO + UNRELEASED + f"## {SEMVER}0\n\n- The tenth alpha {pull(1)}\n"
    only(problems(repo, text), line_of(text, f"## {SEMVER}0"), f"no ## {SEMVER},")


def test_the_version_in_prose_is_no_heading(repo):
    text = INTRO + UNRELEASED + f"Since {SEMVER} it can do more.\n"
    assert any(f"no ## {SEMVER}," in p for p in problems(repo, text))


def test_a_tagged_version_without_metadata_fails(repo):
    text = TAGGED.replace(metadata("0.0.2", "2026-02-03", None) + "\n\n", "")
    repo.tagged(text)
    found = versions.release_notes_problems(SEMVER)
    only(found, line_of(text, "## 0.0.2"), "## 0.0.2 has no metadata line")


def test_the_newest_tagged_version_without_metadata_fails(repo):
    text = TAGGED.replace(metadata(SEMVER, "2026-03-05", "0.0.2") + "\n\n", "")
    repo.tagged(text)
    found = versions.release_notes_problems(SEMVER)
    only(found, line_of(text, f"## {SEMVER}"), f"## {SEMVER} has no metadata line")


def test_the_newest_untagged_version_without_metadata_passes(repo):
    repo.tag("0.0.2", TAGS["0.0.2"])
    text = TAGGED.replace(metadata(SEMVER, "2026-03-05", "0.0.2") + "\n\n", "")
    assert problems(repo, text) == []


def test_the_newest_untagged_version_with_metadata_passes(repo):
    repo.tag("0.0.2", TAGS["0.0.2"])
    assert problems(repo, TAGGED) == []


def test_an_older_version_without_a_tag_fails(repo):
    repo.tag(SEMVER, TAGS[SEMVER])
    found = problems(repo, TAGGED)
    only(found, line_of(TAGGED, "## 0.0.2"), "no tag v0.0.2")


def test_no_tags_at_all_is_one_problem(repo):
    found = problems(repo, TAGGED)
    only(found, line_of(TAGGED, f"## {SEMVER}"), "git fetch --tags")


def test_one_untagged_version_needs_no_tags(repo):
    assert versions.release_notes_problems(SEMVER) == []


def test_a_wrapped_metadata_line_passes(repo):
    text = TAGGED.replace(" · [All changes]", "\n· [All changes]")
    repo.tagged(text)
    assert versions.release_notes_problems(SEMVER) == []


def test_a_malformed_metadata_line_fails(repo):
    text = TAGGED.replace("2026-02-03 · [GitHub release]", "2026-02-03, [GitHub release]")
    repo.tagged(text)
    found = versions.release_notes_problems(SEMVER)
    only(found, line_of(text, "2026-02-03,"), "the metadata line reads YYYY-MM-DD")


def test_a_date_that_is_no_date_fails(repo):
    text = TAGGED.replace("2026-02-03 ·", "2026-02-30 ·")
    repo.tagged(text)
    found = versions.release_notes_problems(SEMVER)
    only(found, line_of(text, "2026-02-30"), "2026-02-30 is no date")


def test_a_wrong_release_link_fails(repo):
    text = TAGGED.replace("releases/tag/v0.0.2", "releases/tag/v0.0.3")
    repo.tagged(text)
    found = versions.release_notes_problems(SEMVER)
    only(
        found, line_of(text, "tag/v0.0.3"), f"the GitHub release link is {REPO}/releases/tag/v0.0.2"
    )


def test_a_compare_link_on_the_oldest_fails(repo):
    text = TAGGED.replace(
        metadata("0.0.2", "2026-02-03", None), metadata("0.0.2", "2026-02-03", "0.0.1")
    )
    repo.tagged(text)
    found = versions.release_notes_problems(SEMVER)
    only(
        found,
        line_of(text, "v0.0.1...v0.0.2"),
        "the oldest version not withdrawn has no All changes link",
    )


def test_a_wrong_previous_fails(repo):
    text = TAGGED.replace("compare/v0.0.2...", "compare/v0.0.1...")
    repo.tagged(text)
    found = versions.release_notes_problems(SEMVER)
    only(found, line_of(text, "compare/v0.0.1"), f"{REPO}/compare/v0.0.2...v{SEMVER}")


def test_a_missing_compare_link_fails(repo):
    text = TAGGED.replace(
        metadata(SEMVER, "2026-03-05", "0.0.2"), metadata(SEMVER, "2026-03-05", None)
    )
    repo.tagged(text)
    found = versions.release_notes_problems(SEMVER)
    only(found, line_of(text, f"tag/v{SEMVER})"), "ends with [All changes]")


## 0.0.2 withdrawn when SEMVER replaced it, so SEMVER has no All changes link
GONE_0_0_2 = TAGGED.replace(
    metadata(SEMVER, "2026-03-05", "0.0.2"), metadata(SEMVER, "2026-03-05", None)
).replace(metadata("0.0.2", "2026-02-03", None), withdrawn("2026-02-03", "2026-03-06", SEMVER))


def test_a_withdrawn_version_needs_no_tag(repo):
    repo.tag(SEMVER, TAGS[SEMVER])
    assert problems(repo, GONE_0_0_2) == []


def test_an_untagged_newest_above_withdrawn_versions_needs_no_tags(repo):
    assert problems(repo, GONE_0_0_2) == []


def test_a_withdrawn_version_whose_tag_is_still_there_passes(repo):
    repo.tagged(GONE_0_0_2)
    assert versions.release_notes_problems(SEMVER) == []


def test_a_compare_link_to_a_withdrawn_version_fails(repo):
    text = GONE_0_0_2.replace(
        metadata(SEMVER, "2026-03-05", None), metadata(SEMVER, "2026-03-05", "0.0.2")
    )
    found = problems(repo, text)
    only(found, line_of(text, "compare/v0.0.2"), "not withdrawn has no All changes link")


def test_the_compare_link_skips_a_withdrawn_version(repo):
    gone = f"## 0.0.3\n\n{withdrawn('2026-02-10', '2026-03-06', SEMVER)}\n\n- Try it {pull(2)}\n\n"
    text = TAGGED.replace("## 0.0.2", gone + "## 0.0.2")
    repo.tagged(text)
    assert versions.release_notes_problems(SEMVER) == []
    wrong = text.replace("compare/v0.0.2...", "compare/v0.0.3...")
    found = problems(repo, wrong)
    only(found, line_of(wrong, "compare/v0.0.3"), f"{REPO}/compare/v0.0.2...v{SEMVER}")


def test_a_withdrawn_version_replaced_by_no_newer_version_fails(repo):
    text = GONE_0_0_2.replace(f"see [{SEMVER}]", "see [0.2.0]")
    found = problems(repo, text)
    only(found, line_of(text, "Withdrawn on"), "0.2.0 is no version above 0.0.2")


def test_a_withdrawn_version_with_a_wrong_anchor_fails(repo):
    text = GONE_0_0_2.replace("(#010-alpha1)", "(#0.1.0-alpha.1)")
    found = problems(repo, text)
    only(found, line_of(text, "Withdrawn on"), f"the link to {SEMVER} is #010-alpha1")


def test_a_withdrawal_before_the_release_fails(repo):
    text = GONE_0_0_2.replace("Withdrawn on 2026-03-06", "Withdrawn on 2026-01-06")
    found = problems(repo, text)
    only(found, line_of(text, "Withdrawn on"), "comes before the release on 2026-02-03")


def test_a_withdrawal_date_that_is_no_date_fails(repo):
    text = GONE_0_0_2.replace("Withdrawn on 2026-03-06", "Withdrawn on 2026-02-30")
    found = problems(repo, text)
    only(found, line_of(text, "Withdrawn on"), "2026-02-30 is no date")


def test_the_anchor_is_the_docs_sites_slug():
    assert versions.anchor("1.0.0-beta.3") == "100-beta3"
    assert versions.anchor(SEMVER) == "010-alpha1"


def plain(text: str) -> str:
    """TAGGED with only the Other changes of the newest version, under its heading."""
    start = text.index("### Breaking changes")
    return text[:start] + text[text.index("### Other changes") :]


def test_other_changes_without_a_warning_heading_fails(repo):
    text = plain(TAGGED)
    repo.tagged(text)
    found = versions.release_notes_problems(SEMVER)
    only(found, line_of(text, "### Other changes"), "only follows a warning heading")


def test_a_plain_list_without_a_warning_heading_passes(repo):
    repo.tagged(plain(TAGGED).replace("### Other changes\n\n", ""))
    assert versions.release_notes_problems(SEMVER) == []


def test_a_warning_heading_after_a_plain_bullet_list_fails(repo):
    text = TAGGED.replace(
        "### Breaking changes", f"- Sort the grid {pull(11)}\n\n### Breaking changes"
    )
    repo.tagged(text)
    found = versions.release_notes_problems(SEMVER)
    only(found, line_of(text, "- Sort the grid"), "under ### Other changes")


def test_warning_headings_out_of_order_fail(repo):
    breaking = TAGGED.index("### Breaking changes")
    security = TAGGED.index("### Security")
    deprecated = TAGGED.index("### Deprecated")
    text = (
        TAGGED[:breaking]
        + TAGGED[security:deprecated]
        + TAGGED[breaking:security]
        + TAGGED[deprecated:]
    )
    repo.tagged(text)
    found = versions.release_notes_problems(SEMVER)
    only(found, line_of(text, "### Breaking changes"), "### Breaking changes is out of order")


def test_an_unknown_subheading_fails(repo):
    text = TAGGED.replace("### Deprecated", "### Fixed")
    repo.tagged(text)
    found = versions.release_notes_problems(SEMVER)
    only(found, line_of(text, "### Fixed"), "### Fixed is not a heading of an entry")


def test_another_heading_level_fails(repo):
    text = TAGGED.replace("### Deprecated", "#### Deprecated")
    repo.tagged(text)
    found = versions.release_notes_problems(SEMVER)
    only(found, line_of(text, "#### Deprecated"), "no headings but ###")


def test_a_heading_without_bullets_fails(repo):
    start = TAGGED.index("### Deprecated")
    text = TAGGED[:start] + "### Deprecated\n\n" + TAGGED[TAGGED.index("### Other changes") :]
    repo.tagged(text)
    found = versions.release_notes_problems(SEMVER)
    only(found, line_of(text, "### Deprecated"), "### Deprecated has no bullets")


def test_a_bullet_without_a_pull_request_link_fails(repo):
    text = TAGGED.replace(f"- Start bwtidy {pull(1)}", "- Start bwtidy")
    repo.tagged(text)
    found = versions.release_notes_problems(SEMVER)
    only(found, line_of(text, "- Start bwtidy"), "ends with its pull request links")


def test_text_after_the_pull_request_link_fails(repo):
    text = TAGGED.replace(f"- Start bwtidy {pull(1)}", f"- Start bwtidy {pull(1)}.")
    repo.tagged(text)
    found = versions.release_notes_problems(SEMVER)
    only(found, line_of(text, "- Start bwtidy"), "ends with its pull request links")


def test_two_pull_request_links_in_one_parentheses_pass(repo):
    assert pull(3, 4) in TAGGED
    repo.tagged()
    assert versions.release_notes_problems(SEMVER) == []


def test_two_pull_request_links_in_two_parentheses_fail(repo):
    text = TAGGED.replace(pull(3, 4), f"{pull(3)} {pull(4)}")
    repo.tagged(text)
    found = versions.release_notes_problems(SEMVER)
    only(found, line_of(text, "keyboard"), "in one pair of parentheses at its end")


def test_a_link_to_another_pull_request_than_it_names_fails(repo):
    text = TAGGED.replace(f"[#1]({REPO}/pull/1)", f"[#1]({REPO}/pull/2)")
    repo.tagged(text)
    found = versions.release_notes_problems(SEMVER)
    only(found, line_of(text, "- Start bwtidy"), "the link #1 points to pull 2")


def test_pull_request_links_wrapped_to_the_next_line_pass(repo):
    repo.tagged(TAGGED.replace(pull(3, 4), pull(3, 4).replace(", ", ",\n  ")))
    assert versions.release_notes_problems(SEMVER) == []


def test_a_nested_bullet_fails(repo):
    text = TAGGED.replace(f"- Start bwtidy {pull(1)}", f"- Start bwtidy\n    - in a grid {pull(1)}")
    repo.tagged(text)
    found = versions.release_notes_problems(SEMVER)
    only(found, line_of(text, "    - in a grid"), "one flat list")


def test_a_paragraph_below_the_bullets_fails(repo):
    text = TAGGED + "\nThat was all.\n"
    repo.tagged(text)
    found = versions.release_notes_problems(SEMVER)
    only(found, line_of(text, "That was all."), "goes above its bullets")


def test_a_heading_in_a_code_block_is_no_heading(repo):
    text = TAGGED.replace(
        "The first alpha brings a lot.",
        "The first alpha brings a lot.\n\n```sh\n## not a heading\n```",
    )
    repo.tagged(text)
    assert versions.release_notes_problems(SEMVER) == []


def test_ci_names_release_notes_without_the_version(repo):
    text = INTRO + UNRELEASED + f"## 0.0.9\n\n- Older {pull(1)}\n"
    repo.release_notes(text)
    assert versions.version_mismatch() == (
        f"docs/releases.md:{line_of(text, '## 0.0.9')}: no ## {SEMVER}, the current version as "
        f"frontend/package.json spells it {RULES}"
    )


def test_ci_names_missing_release_notes(repo):
    (repo.root / "docs" / "releases.md").unlink()
    mismatch = versions.version_mismatch()
    assert mismatch is not None
    assert mismatch.startswith("cannot read docs/releases.md:")


def test_ci_names_both_a_drifted_package_json_and_missing_release_notes(repo):
    repo.package_json(DRIFTED)
    text = INTRO + UNRELEASED + f"## {DRIFTED}\n\n- Too early {pull(1)}\n"
    repo.release_notes(text)
    mismatch = versions.version_mismatch()
    assert mismatch is not None
    assert [line.split(" ")[0] for line in mismatch.splitlines()] == [
        "frontend/package.json",
        f"docs/releases.md:{line_of(text, DRIFTED)}:",
    ]


def test_health_text_is_the_version_and_the_commit():
    assert health.health_text("261111 7db8830") == (
        f"{versions.semver(versions.pyproject_version())} (261111 7db8830)"
    )

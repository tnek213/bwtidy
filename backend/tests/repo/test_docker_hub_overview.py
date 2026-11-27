"""docker/hub-overview.md is the Overview of the image on Docker Hub, which the workflow
.github/workflows/docker-readme.yml copies there (dev/release.md, The Docker Hub overview).

It shares the introduction, the beta warning and the trademark note with README.md (AGENTS.md,
Writing docs). It links only absolutely, as Docker Hub resolves no relative link, and it fits
Docker Hub's limit."""

import re

import yaml
from devlib.paths import ROOT

from tests.support.shared_texts import disclaimer, intro, trademark

OVERVIEW = "docker/hub-overview.md"
WORKFLOW = ROOT / ".github" / "workflows" / "docker-readme.yml"
## the most Docker Hub takes for an overview (the README of peter-evans/dockerhub-description)
LIMIT_BYTES = 25_000

FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,}).*?^ {0,3}\1", re.MULTILINE | re.DOTALL)
INLINE_CODE = re.compile(r"(`+)[^`].*?\1", re.DOTALL)
COMMENT = re.compile(r"<!--.*?-->", re.DOTALL)
## the target of an inline link or picture, of a reference definition, and of an HTML attribute
INLINE_TARGET = re.compile(r"\]\(\s*<?([^)\s>]+)")
REFERENCE_TARGET = re.compile(r"^ {0,3}\[[^\]]+\]:\s*<?(\S+?)>?(?:\s|$)", re.MULTILINE)
HTML_TARGET = re.compile(r"\b(?:href|src)\s*=\s*[\"']?([^\"'\s>]+)")


def overview() -> str:
    return (ROOT / OVERVIEW).read_text()


def readme() -> str:
    return (ROOT / "README.md").read_text()


def sync_step() -> dict:
    workflow = yaml.safe_load(WORKFLOW.read_text())
    steps = workflow["jobs"]["sync"]["steps"]
    action = "peter-evans/dockerhub-description@"
    return next(step for step in steps if step.get("uses", "").startswith(action))


def link_targets(text: str) -> list[str]:
    prose = INLINE_CODE.sub("", FENCE.sub("", COMMENT.sub("", text)))
    return [
        target
        for pattern in (INLINE_TARGET, REFERENCE_TARGET, HTML_TARGET)
        for target in pattern.findall(prose)
    ]


def test_the_workflow_copies_the_overview_to_the_image():
    settings = sync_step()["with"]
    assert settings["readme-filepath"] == OVERVIEW
    assert settings["repository"] == "tnek213/bwtidy"


def test_a_change_to_the_overview_runs_the_workflow():
    ## yaml reads the key on: as True
    workflow = yaml.safe_load(WORKFLOW.read_text())
    push = workflow[True]["push"]
    assert push["branches"] == ["main"]
    assert OVERVIEW in push["paths"]


def test_the_introduction_is_the_one_of_the_readme():
    assert intro(overview()) == intro(readme())


def test_the_warning_is_the_readme_box_as_a_plain_quote():
    box = disclaimer(readme())
    assert box.startswith("> [!WARNING]\n")
    quote = box.removeprefix("> [!WARNING]\n")
    text = COMMENT.sub("", overview())
    start = text.index("> **Made with AI.**")
    assert text[start : text.index("\n\n", start)] == quote
    assert "[!WARNING]" not in text


def test_the_trademark_note_is_the_one_of_the_readme():
    assert trademark(COMMENT.sub("", overview())) == trademark(readme())


def test_every_link_is_absolute():
    targets = link_targets(overview())
    assert targets
    relative = [t for t in targets if not t.startswith("https://")]
    assert relative == []


def test_the_link_check_finds_every_kind_of_link():
    text = (
        "[a](https://x.example/a) ![b](docs/b.png) [c][c]\n\n"
        "[c]: ../c.md\n\n"
        '<a href="d.md">d</a> <img src=e.png alt="e">\n\n'
        "`[e](e.md)`\n\n"
        "```md\n[f](f.md)\n```\n"
    )
    assert link_targets(text) == ["https://x.example/a", "docs/b.png", "../c.md", "d.md", "e.png"]


def test_it_fits_the_size_limit():
    assert len((ROOT / OVERVIEW).read_bytes()) <= LIMIT_BYTES


def test_every_variable_it_names_is_in_the_configuration_reference():
    reference = (ROOT / "docs" / "reference" / "configuration.md").read_text()
    names = set(re.findall(r"\bBWTIDY_[A-Z_]+\b", overview()))
    assert names
    assert sorted(n for n in names if f"`{n}`" not in reference) == []

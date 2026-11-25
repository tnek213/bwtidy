"""README.md (which Docker Hub shows too) and docs/index.md carry the same introduction, the same
alpha disclaimer and the same "At a glance" list (AGENTS.md, Writing docs), so GitHub, Docker Hub
and the docs site say one thing."""

from devlib.paths import ROOT

INTRO_START = "<!-- intro: the same text in README.md and docs/index.md -->\n\n"
INTRO_END = "\n\n<!-- end of intro -->"


def intro(text: str) -> str:
    start = text.index(INTRO_START) + len(INTRO_START)
    end = text.index(INTRO_END, start)
    return text[start:end]


def disclaimer(text: str) -> str:
    start = text.index("> **Alpha software")
    end = text.index("\n\n", start)
    return text[start:end]


def at_a_glance(text: str) -> str:
    start = text.index("## At a glance\n\n") + len("## At a glance\n\n")
    end = text.index("\n\n", start)
    return text[start:end]


def test_the_introduction_is_the_same_text():
    readme = (ROOT / "README.md").read_text()
    home = (ROOT / "docs" / "index.md").read_text()
    assert intro(readme) == intro(home)


def test_the_disclaimer_is_the_same_text():
    readme = (ROOT / "README.md").read_text()
    home = (ROOT / "docs" / "index.md").read_text()
    assert disclaimer(readme) == disclaimer(home)


def test_at_a_glance_is_the_same_list():
    readme = (ROOT / "README.md").read_text()
    home = (ROOT / "docs" / "index.md").read_text()
    assert at_a_glance(readme) == at_a_glance(home)

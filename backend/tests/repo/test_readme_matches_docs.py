"""README.md and docs/index.md carry the same introduction, the same beta warning box and the same
"At a glance" list (AGENTS.md, Writing docs), so GitHub and the docs site say one thing. The Docker
Hub overview shares the first two (test_docker_hub_overview.py)."""

from devlib.paths import ROOT

from tests.support.shared_texts import at_a_glance, disclaimer, intro


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

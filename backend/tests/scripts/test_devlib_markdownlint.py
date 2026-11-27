import shutil
from pathlib import Path

import pytest
from devlib import markdownlint
from devlib.paths import ROOT

CLEAN = "# Title\n\nSome text.\n\n- one\n- two\n"
STAR_BULLET = "# Title\n\nSome text.\n\n* one\n* two\n"


RULES = Path("tools") / "markdownlint" / "rules"
WRAPPED = "BWT001/wrapped-line-list-marker"


@pytest.fixture
def repo(tmp_path) -> Path:
    """A directory with the repo's markdownlint settings and its custom rules, where the tests
    write Markdown files."""
    shutil.copy(ROOT / ".markdownlint-cli2.jsonc", tmp_path)
    shutil.copytree(ROOT / RULES, tmp_path / RULES)
    return tmp_path


def test_a_clean_file_passes(repo):
    (repo / "clean.md").write_text(CLEAN)
    assert markdownlint.lint(repo) is None
    assert markdownlint.check(repo) is None


def test_a_broken_rule_is_named_with_its_file_and_line(repo):
    (repo / "clean.md").write_text(CLEAN)
    (repo / "docs").mkdir()
    (repo / "docs" / "bad.md").write_text(STAR_BULLET)
    findings = markdownlint.check(repo)
    assert findings is not None
    assert "docs/bad.md:5:1 error MD004/ul-style" in findings
    assert "docs/bad.md:6:1 error MD004/ul-style" in findings
    assert "clean.md" not in findings.replace("docs/bad.md", "")
    assert "scripts/ci --fix" in findings


def test_fix_corrects_a_star_bullet_and_the_check_then_passes(repo):
    bad = repo / "bad.md"
    bad.write_text(STAR_BULLET)
    markdownlint.lint(repo, fix=True)
    assert bad.read_text() == CLEAN
    assert markdownlint.check(repo) is None


def test_a_long_line_inline_html_and_a_star_bullet_are_each_named(repo):
    text = "# Title\n\n" + "word " * 16 + "words\n\n<div>x</div>\n\n<span>y</span>\n\n* one\n"
    (repo / "bad.md").write_text(text)
    findings = markdownlint.check(repo)
    assert findings is not None
    assert "bad.md:3:81 error MD013/line-length" in findings
    assert "bad.md:7:1 error MD033/no-inline-html" in findings
    assert "Element: span" in findings
    assert "Element: div" not in findings
    assert "bad.md:9:1 error MD004/ul-style" in findings


def test_what_the_settings_ignore_is_not_linted(repo):
    for folder in ("node_modules/x", ".venv", "site"):
        (repo / folder).mkdir(parents=True)
        (repo / folder / "bad.md").write_text(STAR_BULLET)
    assert markdownlint.check(repo) is None


def test_a_wrapped_line_of_an_item_that_starts_with_a_number_is_named(repo):
    text = "# Title\n\n- bash 3.2 is enough as no command needs bash\n  4. The shell is zsh.\n"
    (repo / "docs").mkdir()
    (repo / "docs" / "bad.md").write_text(text)
    findings = markdownlint.check(repo)
    assert findings is not None
    assert f"docs/bad.md:4:3 error {WRAPPED}" in findings
    assert "Move the line break" in findings


def test_the_same_item_with_the_number_at_the_end_of_the_line_before_passes(repo):
    text = "# Title\n\n- bash 3.2 is enough as no command needs bash 4.\n  The shell is zsh.\n"
    (repo / "good.md").write_text(text)
    assert markdownlint.check(repo) is None


def test_a_wrapped_line_of_an_item_that_starts_with_a_dash_is_named(repo):
    ## CommonMark keeps a dash indented 4 past the item's text in the paragraph, Python-Markdown
    ## starts a nested item there
    text = "# Title\n\n- The tags are the first\n      - and the last.\n"
    (repo / "bad.md").write_text(text)
    findings = markdownlint.check(repo)
    assert findings is not None
    assert f"bad.md:4:7 error {WRAPPED}" in findings


@pytest.mark.parametrize(
    "text",
    [
        "# Title\n\n1. one\n2. two\n3. three\n",
        "# Title\n\n- one\n    - nested\n    - and more\n- two\n",
        "# Title\n\n1. Run this:\n\n    ```text\n    3. three\n    4. four\n    ```\n",
        "# Title\n\n- An item.\n\n    A second paragraph of the\n    4. item stays one.\n",
    ],
    ids=["numbered list", "nested bullet list", "code in an item", "later paragraph"],
)
def test_real_list_items_code_and_a_later_paragraph_pass(repo, text):
    (repo / "good.md").write_text(text)
    assert markdownlint.check(repo) is None


def test_it_installs_its_npm_folder_when_needed(monkeypatch):
    calls = []
    monkeypatch.setattr(markdownlint.locks, "npm_install_needed", lambda project, root: True)
    monkeypatch.setattr(markdownlint.locks, "write_npm_stamp", lambda project, root: None)
    monkeypatch.setattr(
        markdownlint.subprocess, "run", lambda argv, cwd, check: calls.append((argv, cwd))
    )
    markdownlint.install(ROOT)
    assert calls == [(["npm", "ci", "--no-fund", "--no-audit"], ROOT / "tools" / "markdownlint")]

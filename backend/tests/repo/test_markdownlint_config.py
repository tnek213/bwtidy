""".markdownlint-cli2.jsonc holds the Markdown style the maintainers chose (AGENTS.md, Writing
docs), so a change to a rule is deliberate. scripts/ci lints with it (devlib/markdownlint.py) and
the VS Code extension markdownlint reads it too."""

import json
import subprocess

import pytest
from devlib.paths import ROOT

CONFIG = ROOT / ".markdownlint-cli2.jsonc"


def settings() -> dict:
    ## the file's comments are whole lines, which JSON does not allow
    lines = [line for line in CONFIG.read_text().splitlines() if not line.lstrip().startswith("//")]
    return json.loads("\n".join(lines))


@pytest.mark.parametrize(
    ("rule", "value"),
    [
        ("MD003", {"style": "atx"}),
        ("MD004", {"style": "dash"}),
        ## Zensical nests a list only when it is indented by 4 spaces
        ("MD007", {"indent": 4}),
        (
            "MD013",
            {"line_length": 80, "code_blocks": False, "tables": False, "headings": False},
        ),
        ("MD024", {"siblings_only": True}),
        ("MD029", {"style": "ordered"}),
        ## the cards of a landing page are the one HTML element
        ("MD033", {"allowed_elements": ["div"]}),
        ("MD046", {"style": "fenced"}),
        ("MD048", {"style": "backtick"}),
        ("MD049", {"style": "underscore"}),
        ("MD050", {"style": "asterisk"}),
    ],
)
def test_a_rule_has_the_chosen_setting(rule, value):
    assert settings()["config"][rule] == value


def test_every_other_rule_keeps_its_default():
    config = settings()["config"]
    assert config["default"] is True
    assert all(value is not False for value in config.values())


def test_only_two_overrides_turn_a_rule_off_for_named_files():
    overrides = {tuple(o["filter"]): (o["config"], o["combine"]) for o in settings()["overrides"]}
    assert overrides == {
        ("docker/hub-overview.md",): ({"MD025": False}, "merge"),
        (".github/pull_request_template.md", "CLAUDE.md"): ({"MD041": False}, "merge"),
    }


def test_every_markdown_file_of_the_repo_is_linted():
    assert settings()["globs"] == ["**/*.md"]
    tracked = subprocess.run(
        ["git", "ls-files", "*.md"], cwd=ROOT, capture_output=True, text=True, check=True
    ).stdout.split()
    ignores = settings()["ignores"]
    ## an ignore names a folder of packages, caches or build output, none of the repo's own text
    assert all(pattern.endswith("/**") for pattern in ignores)
    folders = [pattern.removesuffix("/**").removeprefix("**/") for pattern in ignores]
    hit = [path for path in tracked for folder in folders if f"/{folder}/" in f"/{path}"]
    assert hit == []

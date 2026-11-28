"""scripts/mcp-reference: docs/reference/mcp-tools.md from the tools the MCP server registers."""

import re
import shutil
from types import SimpleNamespace

import pytest
from devlib import markdownlint
from devlib.paths import ROOT
from mcp.types import ToolAnnotations


@pytest.fixture(scope="module")
def mcp_reference(load_script):
    return load_script("mcp-reference")


@pytest.fixture(scope="module")
def tools(mcp_reference):
    return mcp_reference.registered_tools()


@pytest.fixture(scope="module")
def page(tools, mcp_reference) -> str:
    return mcp_reference.rendered(tools)


def section(page: str, name: str) -> str:
    start = page.index(f"\n### {name}\n")
    end = page.find("\n### ", start + 1)
    return page[start : end if end != -1 else len(page)]


def test_the_page_names_every_tool_the_server_registers(tools, page):
    headings = re.findall(r"^### (\S+)$", page, re.MULTILINE)
    assert sorted(headings) == sorted(tool.name for tool in tools)
    assert len(headings) == len(tools)


def test_a_tools_kind_follows_its_annotations(tools, page):
    for tool in tools:
        hints = tool.annotations
        expected = (
            "read" if hints.read_only_hint else "end" if hints.destructive_hint else "propose"
        )
        article = "An" if expected == "end" else "A"
        assert f"\n{article} {expected} tool.\n" in section(page, tool.name), tool.name


def test_every_kind_has_a_tool(tools, mcp_reference):
    assert {mcp_reference.kind(tool) for tool in tools} == {"read", "propose", "end"}


def test_annotations_of_no_kind_are_refused(mcp_reference):
    tool = SimpleNamespace(name="odd", annotations=ToolAnnotations(read_only_hint=False))
    with pytest.raises(ValueError, match="odd"):
        mcp_reference.kind(tool)


def test_each_parameter_has_a_row_with_its_type(tools, page):
    tool = next(tool for tool in tools if tool.name == "propose_set_uris")
    text = section(page, tool.name)
    for name in tool.input_schema["properties"]:
        assert f"| `{name}` |" in text
    assert "| `uris` | list of `UriInput` | required |" in text
    assert "Each `UriInput`:" in text


def test_markdown_in_a_description_shows_as_text(mcp_reference):
    assert mcp_reference.text("fields.<name>, a | b") == "fields.&lt;name&gt;, a \\| b"
    assert mcp_reference.text("*x* and list_items") == "\\*x\\* and list_items"
    assert mcp_reference.text("_x_") == "\\_x\\_"


def test_a_code_span_stays_code(mcp_reference):
    assert mcp_reference.text("`fields.<name>` or *x*") == "`fields.<name>` or \\*x\\*"
    assert mcp_reference.text("a lone ` stays") == "a lone \\` stays"


def test_a_description_keeps_its_paragraphs_and_lists(mcp_reference):
    rendered = mcp_reference.description("Each has:\n\n- `id`: its id.\n- a *b*\n\nLast\nline.")
    assert rendered == ["Each has:", "", "- `id`: its id.", "- a \\*b\\*", "", "Last line.", ""]


def test_the_parameters_follow_their_label(page):
    text = section(page, "list_items")
    assert "\nParameters:\n\n| Parameter | Type | Default | Description |\n" in text
    assert "\n- `secretColumns`: the secret columns that hold a value.\n" in text


def test_check_fails_on_a_changed_file(tmp_path, monkeypatch, capsys, page, mcp_reference):
    output = tmp_path / "mcp-tools.md"
    monkeypatch.setattr(mcp_reference, "ROOT", tmp_path)
    monkeypatch.setattr(mcp_reference, "OUTPUT", output)

    assert mcp_reference.main([]) == 0
    assert output.read_text() == page
    assert mcp_reference.main(["--check"]) == 0

    output.write_text(page.replace("A read tool.", "A propose tool.", 1))
    capsys.readouterr()
    assert mcp_reference.main(["--check"]) == 1
    assert "mcp-tools.md is stale, run scripts/mcp-reference" in capsys.readouterr().err


def test_check_fails_without_the_file(tmp_path, monkeypatch, mcp_reference):
    monkeypatch.setattr(mcp_reference, "ROOT", tmp_path)
    monkeypatch.setattr(mcp_reference, "OUTPUT", tmp_path / "mcp-tools.md")
    assert mcp_reference.main(["--check"]) == 1


def test_a_long_paragraph_wraps_at_80_without_breaking_a_code_span(mcp_reference):
    rendered = mcp_reference.description("word " * 15 + "`a code span` and more words after it.")
    assert rendered == [
        "word word word word word word word word word word word word word word word",
        "`a code span` and more words after it.",
        "",
    ]
    long_code = "x " + "`" + "y " * 45 + "z`" + " end"
    assert mcp_reference.description(long_code) == ["x", "`" + "y " * 45 + "z`", "end", ""]


def test_a_long_list_entry_wraps_under_its_text(mcp_reference):
    rendered = mcp_reference.description("- " + "item " * 20 + "end")
    assert rendered == [
        "- item item item item item item item item item item item item item item item",
        "  item item item item item end",
        "",
    ]


def test_a_word_that_starts_a_list_never_starts_a_line(mcp_reference):
    rendered = mcp_reference.description("word " * 15 + "words - more and 1. after")
    assert all(not re.match(r"(- |\d+\. )", line) for line in rendered)
    assert " ".join(rendered).split() == ("word " * 15 + "words - more and 1. after").split()


def test_the_page_follows_the_markdownlint_rules(tmp_path, page):
    shutil.copy(ROOT / ".markdownlint-cli2.jsonc", tmp_path)
    rules = ROOT.joinpath("tools", "markdownlint", "rules")
    shutil.copytree(rules, tmp_path / rules.relative_to(ROOT))
    (tmp_path / "mcp-tools.md").write_text(page)
    assert markdownlint.check(tmp_path) is None

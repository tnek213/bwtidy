"""scripts/mcp-reference: docs/reference/mcp-tools.md from the tools the MCP server registers."""

import re
from types import SimpleNamespace

import pytest
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
        assert f"\nKind: {expected}.\n" in section(page, tool.name), tool.name


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


def test_check_fails_on_a_changed_file(tmp_path, monkeypatch, capsys, page, mcp_reference):
    output = tmp_path / "mcp-tools.md"
    monkeypatch.setattr(mcp_reference, "ROOT", tmp_path)
    monkeypatch.setattr(mcp_reference, "OUTPUT", output)

    assert mcp_reference.main([]) == 0
    assert output.read_text() == page
    assert mcp_reference.main(["--check"]) == 0

    output.write_text(page.replace("Kind: read.", "Kind: propose.", 1))
    capsys.readouterr()
    assert mcp_reference.main(["--check"]) == 1
    assert "mcp-tools.md is stale, run scripts/mcp-reference" in capsys.readouterr().err


def test_check_fails_without_the_file(tmp_path, monkeypatch, mcp_reference):
    monkeypatch.setattr(mcp_reference, "ROOT", tmp_path)
    monkeypatch.setattr(mcp_reference, "OUTPUT", tmp_path / "mcp-tools.md")
    assert mcp_reference.main(["--check"]) == 1

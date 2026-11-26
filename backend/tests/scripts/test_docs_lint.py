import subprocess
from pathlib import Path

import pytest


@pytest.fixture(scope="module")
def docs_lint(load_script):
    return load_script("docs-lint")


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    subprocess.run(["git", "init", "--quiet"], cwd=tmp_path, check=True)
    return tmp_path


def write(repo: Path, name: str, text: str = "x") -> None:
    path = repo / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def test_clean_repo_exits_0(repo, monkeypatch, capsys, docs_lint):
    write(repo, "docs/images/a.png")
    write(repo, "docs/images/b.png")
    write(repo, "README.md", '![a](docs/images/a.png)\n\n![b](docs/images/b.png "Title")\n')
    monkeypatch.setattr(docs_lint, "ROOT", repo)
    assert docs_lint.main([]) == 0
    assert capsys.readouterr().out == ""


def test_missing_inline_image(repo, docs_lint):
    write(repo, "README.md", "intro\n\n![shot](docs/images/gone.png)\n")
    assert docs_lint.lint(repo) == ["README.md:3: missing image docs/images/gone.png"]


def test_missing_reference_image(repo, docs_lint):
    write(repo, "README.md", "![shot][s]\n\n[s]: docs/images/gone.png\n")
    assert docs_lint.lint(repo) == ["README.md:1: missing image docs/images/gone.png"]


def test_missing_img_tag(repo, docs_lint):
    write(repo, "README.md", 'a\n<img alt="x"\n  src="docs/images/gone.png" width="10">\n')
    assert docs_lint.lint(repo) == ["README.md:2: missing image docs/images/gone.png"]


def test_link_in_code_is_ignored(repo, docs_lint):
    write(
        repo,
        "README.md",
        "```md\n![a](nope.png)\n```\n\nSee `![b](nope.png)` here.\n",
    )
    write(repo, "OTHER.md", "~~~\n![c](nope.png)\n~~~\n")
    assert docs_lint.lint(repo) == []


def test_url_and_fragment_links_are_ignored(repo, docs_lint):
    write(
        repo,
        "README.md",
        "![a](http://example.test/a.png) ![b](https://example.test/b.png)\n"
        "![c](data:image/png;base64,AAAA) ![d](#top)\n",
    )
    assert docs_lint.lint(repo) == []


def test_pictures_without_alt_are_reported(repo, docs_lint):
    write(repo, "a.png")
    write(
        repo,
        "README.md",
        "![](a.png)\n"
        "![   ](a.png)\n"
        "![][r]\n"
        '<img src="a.png">\n'
        '<img src="a.png" alt="">\n'
        "![](https://example.test/x.png)\n"
        "\n[r]: a.png\n",
    )
    assert docs_lint.lint(repo) == [f"README.md:{n}: picture without alt text" for n in range(1, 7)]


def test_pictures_with_alt_are_accepted(repo, docs_lint):
    write(repo, "a.png")
    write(
        repo,
        "README.md",
        "![a](a.png)\n![b][r]\n<img src='a.png' alt='c'>\n<img alt=d src=a.png>\n"
        "\n```md\n![](x.png)\n```\n\nSee `![](x.png)` here.\n\n[r]: a.png\n",
    )
    assert docs_lint.lint(repo) == []


def test_link_is_resolved_relative_to_its_file(repo, docs_lint):
    write(repo, "docs/images/a.png")
    write(repo, "docs/guide/page.md", "![a](../images/a.png#frag)\n![b](/docs/images/a.png?x=1)\n")
    assert docs_lint.lint(repo) == []
    write(repo, "docs/guide/bad.md", "![a](docs/images/a.png)\n")
    assert docs_lint.lint(repo) == ["docs/guide/bad.md:1: missing image docs/images/a.png"]


def test_orphan_image_is_reported(repo, docs_lint):
    write(repo, "docs/images/linked.png")
    write(repo, "docs/images/orphan.png")
    write(repo, "README.md", "![l](docs/images/linked.png)\n")
    assert docs_lint.lint(repo) == ["docs/images/orphan.png: not linked from any markdown file"]


def test_ignored_files_do_not_count(repo, monkeypatch, capsys, docs_lint):
    write(repo, ".gitignore", "node_modules/\n")
    write(repo, "node_modules/pkg/README.md", "![x](missing.png)\n")
    monkeypatch.setattr(docs_lint, "ROOT", repo)
    assert docs_lint.main([]) == 0


def test_main_prints_problems_and_exits_1(repo, monkeypatch, capsys, docs_lint):
    write(repo, "README.md", "![g](gone.png)\n")
    monkeypatch.setattr(docs_lint, "ROOT", repo)
    assert docs_lint.main([]) == 1
    assert capsys.readouterr().out == "README.md:1: missing image gone.png\n"


STYLE = "(AGENTS.md: split the sentence or use a comma, colon or parentheses)"


def test_missing_page_link(repo, docs_lint):
    write(
        repo,
        "README.md",
        "See [the guide](docs/gone.md) and [ref][r] and\n"
        "<a href='x/gone.md'>a</a>.\n\n[r]: other.md\n",
    )
    assert docs_lint.lint(repo) == [
        "README.md:1: missing link docs/gone.md",
        "README.md:1: missing link other.md",
        "README.md:2: missing link x/gone.md",
    ]


def test_link_to_existing_page(repo, docs_lint):
    write(repo, "docs/a.md", "# A\n\n[b](b.md) [root](/README.md) [s](../scripts/ci)\n")
    write(repo, "docs/b.md", "# B\n")
    write(repo, "README.md", "[a](docs/a.md) [self](README.md) [x][y]\n\n[y]: docs/b.md\n")
    write(repo, "scripts/ci", "#!/bin/sh\n")
    assert docs_lint.lint(repo) == []


def test_image_inside_link_is_checked_once(repo, docs_lint):
    write(repo, "page.md", "# P\n")
    write(repo, "README.md", "[![shot](gone.png)](page.md)\n")
    assert docs_lint.lint(repo) == ["README.md:1: missing image gone.png"]


def test_missing_anchor(repo, docs_lint):
    write(repo, "docs/a.md", "# Title\n\n## Why, and how?\n\nSetext\n======\n")
    write(
        repo,
        "README.md",
        "[ok](docs/a.md#why-and-how) [ok2](docs/a.md#setext) [bad](docs/a.md#nope)\n\n"
        "# Top\n\n[up](#top) [bad](#bottom)\n",
    )
    assert docs_lint.lint(repo) == [
        "README.md:1: no heading for #nope in docs/a.md",
        "README.md:5: no heading for #bottom in README.md",
    ]


def test_anchor_with_repeated_heading(repo, docs_lint):
    write(repo, "a.md", "# Setup\n\n## Usage\n\n# Setup\n\n# Setup\n")
    write(
        repo, "README.md", "[1](a.md#setup) [2](a.md#setup-1) [3](a.md#setup-2) [4](a.md#setup-3)\n"
    )
    assert docs_lint.lint(repo) == ["README.md:1: no heading for #setup-3 in a.md"]


def test_heading_in_fence_is_no_anchor(repo, docs_lint):
    write(repo, "a.md", "# Real\n\n```md\n# Fake\n```\n")
    write(repo, "README.md", "[r](a.md#real) [f](a.md#fake)\n")
    assert docs_lint.lint(repo) == ["README.md:1: no heading for #fake in a.md"]


def test_page_link_in_code_is_ignored(repo, docs_lint):
    write(repo, "README.md", "```md\n[a](nope.md)\n```\n\nSee `[b](nope.md#x)` here.\n")
    assert docs_lint.lint(repo) == []


def test_url_links_are_skipped(repo, docs_lint):
    write(
        repo,
        "README.md",
        "[a](http://example.test/x.md#y) [b](https://example.test) [c](mailto:a@example.test)\n",
    )
    assert docs_lint.lint(repo) == []


def test_semicolon_in_prose_is_reported(repo, docs_lint):
    write(repo, "README.md", "One; two.\n\nfine\nthree; four\n")
    assert docs_lint.lint(repo) == [
        f"README.md:1: semicolon {STYLE}",
        f"README.md:4: semicolon {STYLE}",
    ]


def test_semicolon_in_code_comment_and_link_target_is_ignored(repo, docs_lint):
    write(
        repo,
        "README.md",
        "Run `a; b` now.\n\n```sh\na; b\n```\n\n<!-- a; b\n   c; d -- e -->\n\n"
        "[x](https://example.test/?a=1;b=2) <https://example.test/a;b>\n\n"
        '<a href="https://example.test/?a;b">y</a>\n',
    )
    assert docs_lint.lint(repo) == []


def test_em_dash_is_reported(repo, docs_lint):
    write(repo, "README.md", "One — two.\n")
    assert docs_lint.lint(repo) == [f"README.md:1: em dash {STYLE}"]


def test_double_hyphen_dash_is_reported(repo, docs_lint):
    write(repo, "README.md", "One -- two.\n\nRun `--help` or x --flag or a--b.\n")
    assert docs_lint.lint(repo) == [f"README.md:1: double hyphen as a dash {STYLE}"]


def test_html_entity_is_not_a_semicolon(repo, docs_lint):
    write(repo, "README.md", "Fish &amp; chips &#169; &lt;tag&gt; &#x27;\n")
    assert docs_lint.lint(repo) == []

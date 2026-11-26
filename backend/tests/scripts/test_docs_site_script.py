import shutil
import subprocess
import tomllib
from pathlib import Path

import pytest

from bwtidy.backups.collect import RESTORE_DOC_COMMIT

BANNER = "<p data-disclaimer>bwtidy is beta software</p>"
REAL_ROOT = Path(__file__).resolve().parents[3]
COMMIT = "261111 7db8830"


@pytest.fixture(scope="module")
def docs_site(load_script):
    return load_script("docs-site")


def write(root: Path, name: str, text: str) -> None:
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


@pytest.fixture
def repo(tmp_path: Path, monkeypatch, docs_site) -> Path:
    root = tmp_path / "repo"
    nav = '["index.md", "how-to/run.md", "restore.md"]'
    write(root, "zensical.toml", f"[project]\nnav = {nav}\n")
    write(root, "docs/index.md", "# Home\n")
    write(root, "docs/how-to/run.md", "# Run\n")
    write(root, "docs/restore.md", f"# Restore\n\n{RESTORE_DOC_COMMIT} The project: there\n")
    write(root, "overrides/main.html", "{% extends 'base.html' %}\n")
    write(root, "backend/pyproject.toml", '[project]\nname = "bwtidy"\nversion = "0.1.0a1"\n')
    version = root / "backend" / "src" / "bwtidy" / "version.py"
    version.parent.mkdir(parents=True)
    shutil.copy(REAL_ROOT / "backend" / "src" / "bwtidy" / "version.py", version)
    monkeypatch.setattr(docs_site, "ROOT", root)
    ## the commit as the Dockerfile passes it, so no test runs git
    monkeypatch.setenv("BWTIDY_COMMIT", COMMIT)
    return root


def fake_zensical(monkeypatch, pages: dict[str, str], docs_site) -> dict[str, str]:
    """Stand in for Zensical: write the given pages into site/ of the build directory.

    Returns what it saw: the restore page it was handed, under "restore.md", and the config,
    under "zensical.toml"."""
    seen: dict[str, str] = {}

    def run(cmd, cwd):
        assert (Path(cwd) / "overrides" / "main.html").is_file()
        seen["restore.md"] = (Path(cwd) / "docs" / "restore.md").read_text()
        seen["zensical.toml"] = (Path(cwd) / "zensical.toml").read_text()
        for name, text in pages.items():
            write(Path(cwd) / "site", name, text)
        return subprocess.CompletedProcess(cmd, 0)

    monkeypatch.setattr(docs_site.subprocess, "run", run)
    return seen


def test_without_banner_names_the_bare_pages(tmp_path, docs_site):
    write(tmp_path, "index.html", f"<body>{BANNER}</body>")
    write(tmp_path, "how-to/run/index.html", "<body>no banner</body>")
    write(tmp_path, "assets/x.css", "not a page")
    assert docs_site.without_banner(tmp_path) == ["how-to/run/index.html"]


def test_build_fails_naming_a_page_without_the_banner(repo, monkeypatch, capsys, docs_site):
    fake_zensical(
        monkeypatch,
        {"index.html": f"<body>{BANNER}</body>", "how-to/run/index.html": "<body></body>"},
        docs_site,
    )
    assert docs_site.build(None) == 1
    err = capsys.readouterr().err
    assert "how-to/run/index.html has no disclaimer banner" in err
    assert "docs-site: index.html" not in err


def test_build_passes_when_every_page_has_the_banner(repo, monkeypatch, tmp_path, docs_site):
    fake_zensical(
        monkeypatch,
        {"index.html": f"<body>{BANNER}</body>", "how-to/run/index.html": f"<body>{BANNER}</body>"},
        docs_site,
    )
    target = tmp_path / "out"
    assert docs_site.build(target) == 0
    assert (target / "how-to" / "run" / "index.html").is_file()


def test_build_refuses_a_directory_that_is_not_a_build(
    repo, monkeypatch, tmp_path, capsys, docs_site
):
    fake_zensical(monkeypatch, {"index.html": f"<body>{BANNER}</body>"}, docs_site)
    target = tmp_path / "work"
    write(target, "notes.txt", "keep me")
    assert docs_site.build(target) == 1
    assert (target / "notes.txt").read_text() == "keep me"
    assert "not empty and not a previous build" in capsys.readouterr().err


def test_build_refuses_a_file_as_the_target(repo, monkeypatch, tmp_path, docs_site):
    fake_zensical(monkeypatch, {"index.html": f"<body>{BANNER}</body>"}, docs_site)
    target = tmp_path / "file"
    target.write_text("x")
    assert docs_site.build(target) == 1
    assert target.read_text() == "x"


def test_build_into_an_empty_directory(repo, monkeypatch, tmp_path, docs_site):
    fake_zensical(monkeypatch, {"index.html": f"<body>{BANNER}</body>"}, docs_site)
    target = tmp_path / "out"
    target.mkdir()
    assert docs_site.build(target) == 0
    assert (target / "index.html").is_file()


def test_build_replaces_a_previous_build(repo, monkeypatch, tmp_path, docs_site):
    fake_zensical(monkeypatch, {"index.html": f"<body>{BANNER}</body>"}, docs_site)
    target = tmp_path / "out"
    write(target, "index.html", "old")
    write(target, "sitemap.xml", "old")
    write(target, "gone.html", "old")
    assert docs_site.build(target) == 0
    assert BANNER in (target / "index.html").read_text()
    assert not (target / "gone.html").exists()
    assert [p.name for p in tmp_path.iterdir() if p.name.startswith(".")] == []


def test_a_failed_build_leaves_the_previous_one(repo, monkeypatch, tmp_path, docs_site):
    fake_zensical(monkeypatch, {"index.html": "<body>no banner</body>"}, docs_site)
    target = tmp_path / "out"
    write(target, "index.html", "old")
    write(target, "sitemap.xml", "old")
    assert docs_site.build(target) == 1
    assert (target / "index.html").read_text() == "old"


def test_a_failure_while_putting_the_site_in_place_restores_the_old_one(
    repo, monkeypatch, tmp_path, docs_site
):
    fake_zensical(monkeypatch, {"index.html": f"<body>{BANNER}</body>"}, docs_site)
    target = tmp_path / "out"
    write(target, "index.html", "old")
    write(target, "sitemap.xml", "old")
    real = docs_site.os.replace

    def replace(src, dst):
        if Path(src).name == "site":
            raise OSError("forced")
        real(src, dst)

    monkeypatch.setattr(docs_site.os, "replace", replace)
    with pytest.raises(OSError, match="forced"):
        docs_site.build(target)
    assert (target / "index.html").read_text() == "old"
    assert (target / "sitemap.xml").read_text() == "old"
    assert sorted(p.name for p in tmp_path.iterdir()) == ["out", "repo"]


def test_the_restore_page_names_the_version_the_site_is_built_for(repo, monkeypatch, docs_site):
    """As every backup's RESTORE.md names the image's version (backups/collect.py)."""
    seen = fake_zensical(monkeypatch, {"index.html": f"<body>{BANNER}</body>"}, docs_site)
    assert docs_site.build(None) == 0
    assert seen["restore.md"] == (
        f"# Restore\n\nWritten for bwtidy 0.1.0-alpha.1 ({COMMIT}). The project: there\n"
    )
    assert RESTORE_DOC_COMMIT in (repo / "docs" / "restore.md").read_text()


def test_without_a_commit_the_restore_page_names_the_version_alone(repo, monkeypatch, docs_site):
    monkeypatch.delenv("BWTIDY_COMMIT")
    monkeypatch.setattr(docs_site, "last_commit", lambda: None)
    seen = fake_zensical(monkeypatch, {"index.html": f"<body>{BANNER}</body>"}, docs_site)
    assert docs_site.build(None) == 0
    assert "Written for bwtidy 0.1.0-alpha.1. The project" in seen["restore.md"]


def built_extra(monkeypatch, docs_site) -> dict:
    """The table extra of the config that build hands Zensical."""
    seen = fake_zensical(monkeypatch, {"index.html": f"<body>{BANNER}</body>"}, docs_site)
    assert docs_site.build(None) == 0
    return tomllib.loads(seen["zensical.toml"])["project"]["extra"]


def test_the_config_names_the_version_and_its_semver_alone(repo, monkeypatch, docs_site):
    monkeypatch.delenv("BWTIDY_STARS", raising=False)
    extra = built_extra(monkeypatch, docs_site)
    assert extra["bwtidy_version"] == f"0.1.0-alpha.1 ({COMMIT})"
    assert extra["bwtidy_release"] == "0.1.0-alpha.1"
    assert "bwtidy_stars" not in extra


def test_the_config_holds_the_star_count_from_the_environment(repo, monkeypatch, docs_site):
    monkeypatch.setenv("BWTIDY_STARS", "42")
    assert built_extra(monkeypatch, docs_site)["bwtidy_stars"] == 42


@pytest.mark.parametrize("value", ["", "4x"])
def test_the_config_leaves_out_a_star_count_that_is_not_digits(repo, monkeypatch, docs_site, value):
    monkeypatch.setenv("BWTIDY_STARS", value)
    assert "bwtidy_stars" not in built_extra(monkeypatch, docs_site)


def test_serve_hands_zensical_the_same_extra(repo, monkeypatch, docs_site):
    monkeypatch.setenv("BWTIDY_STARS", "42")
    seen: dict[str, str] = {}

    def run(cmd, cwd):
        seen["config"] = Path(cmd[cmd.index("-f") + 1]).read_text()
        return subprocess.CompletedProcess(cmd, 0)

    monkeypatch.setattr(docs_site.subprocess, "run", run)
    assert docs_site.serve([]) == 0
    extra = tomllib.loads(seen["config"])["project"]["extra"]
    assert extra["bwtidy_release"] == "0.1.0-alpha.1"
    assert extra["bwtidy_stars"] == 42
    assert not (repo / docs_site.SERVE_CONFIG).exists()


def test_build_fails_when_the_restore_page_lacks_the_line(repo, monkeypatch, capsys, docs_site):
    write(repo, "docs/restore.md", "# Restore\n\nWritten for bwtidy.\n")
    fake_zensical(monkeypatch, {"index.html": f"<body>{BANNER}</body>"}, docs_site)
    assert docs_site.build(None) == 1
    assert "docs/restore.md has no line 'Written for bwtidy COMMIT.'" in capsys.readouterr().err


def test_the_line_is_the_one_backups_fill_in(docs_site):
    assert docs_site.RESTORE_DOC_COMMIT == RESTORE_DOC_COMMIT
    assert RESTORE_DOC_COMMIT in (REAL_ROOT / "docs" / "restore.md").read_text()


def test_last_commit_is_the_checkouts_in_the_image_workflows_form(docs_site):
    commit = docs_site.last_commit()
    if not (REAL_ROOT / ".git").exists():
        pytest.skip("not a git checkout")
    assert commit is not None
    assert len(commit.split()[0]) == 6 and len(commit.split()[1]) >= 7


PICTURE = '<p><img alt="x" src="a.png"></p>'
CODE = '<div class="highlight"><pre><code>x</code></pre></div>'


def page(*parts: str) -> str:
    return "<body>\n" + "\n".join(parts) + "\n</body>"


def test_broken_lists_finds_a_list_that_restarts_after_a_picture(tmp_path, docs_site):
    html = page("<ol><li>One</li><li>Two</li></ol>", PICTURE, "<ol><li>Three</li></ol>")
    write(tmp_path, "tutorial/a/index.html", html)
    assert docs_site.broken_lists(tmp_path) == [
        "tutorial/a/index.html:4: an ordered list starts at 1 again after a picture or code block"
    ]


def test_broken_lists_finds_a_list_that_restarts_after_a_code_block(tmp_path, docs_site):
    write(tmp_path, "a/index.html", page("<ol><li>One</li></ol>", CODE, "<ol><li>Two</li></ol>"))
    assert len(docs_site.broken_lists(tmp_path)) == 1


def test_broken_lists_passes_one_list_with_a_picture_inside(tmp_path, docs_site):
    item = f"<li><p>One</p>{PICTURE}{CODE}</li><li>Two</li>"
    write(tmp_path, "a/index.html", page(f"<ol>{item}</ol>"))
    assert docs_site.broken_lists(tmp_path) == []


def test_broken_lists_passes_two_lists_with_text_between(tmp_path, docs_site):
    html = page("<ol><li>One</li></ol>", "<p>Then, a new part.</p>", "<ol><li>One</li></ol>")
    write(tmp_path, "a/index.html", html)
    assert docs_site.broken_lists(tmp_path) == []


def test_broken_lists_passes_a_list_with_a_start_number(tmp_path, docs_site):
    html = page("<ol><li>One</li></ol>", PICTURE, '<ol start="2"><li>Two</li></ol>')
    write(tmp_path, "a/index.html", html)
    assert docs_site.broken_lists(tmp_path) == []


def test_build_fails_naming_the_page_and_line_of_a_broken_list(
    repo, monkeypatch, capsys, docs_site
):
    broken = page("<p data-disclaimer></p><ol><li>One</li></ol>", PICTURE, "<ol><li>Two</li></ol>")
    fake_zensical(monkeypatch, {"how-to/run/index.html": broken}, docs_site)
    assert docs_site.build(None) == 1
    assert (
        "docs-site: how-to/run/index.html:4: an ordered list starts at 1" in capsys.readouterr().err
    )


def test_external_loads_names_each_fetch_from_another_host(tmp_path, docs_site):
    html = page(
        '<script src="https://cdn.example/x.js"></script>',
        '<link rel="stylesheet" href="//fonts.example/a.css">',
        '<p><img alt="x" src="https://cdn.example/e.svg"></p>',
        '<picture><source srcset="a.png 1x, https://cdn.example/b.png 2x"></picture>',
        "<style>.x { background: url('https://cdn.example/c.png') }</style>",
        '<p style="background: url(https://cdn.example/d.png)">x</p>',
        '<object data="https://cdn.example/f.svg"></object>',
    )
    write(tmp_path, "a/index.html", html)
    assert docs_site.external_loads(tmp_path) == [
        "a/index.html:2: loads https://cdn.example/x.js from another host",
        "a/index.html:3: loads //fonts.example/a.css from another host",
        "a/index.html:4: loads https://cdn.example/e.svg from another host",
        "a/index.html:5: loads https://cdn.example/b.png from another host",
        "a/index.html:6: loads https://cdn.example/c.png from another host",
        "a/index.html:7: loads https://cdn.example/d.png from another host",
        "a/index.html:8: loads https://cdn.example/f.svg from another host",
    ]


def test_external_loads_passes_links_out_and_the_sites_own_files(tmp_path, docs_site):
    html = page(
        '<a href="https://github.com/tnek213/bwtidy">repo</a>',
        '<link rel="next" href="https://other.example/next/">',
        '<link rel="stylesheet" href="../assets/main.css">',
        '<script src="/docs/assets/bundle.js"></script>',
        PICTURE,
        '<img alt="" src="data:image/png;base64,AAAA">',
        "<style>.x { background: url(../a.png) }</style>",
    )
    write(tmp_path, "a/index.html", html)
    assert docs_site.external_loads(tmp_path) == []


def test_build_fails_naming_a_page_that_loads_from_another_host(
    repo, monkeypatch, capsys, docs_site
):
    remote = page(BANNER, '<script src="https://cdn.example/x.js"></script>')
    fake_zensical(monkeypatch, {"index.html": remote}, docs_site)
    assert docs_site.build(None) == 1
    err = capsys.readouterr().err
    assert "docs-site: index.html:3: loads https://cdn.example/x.js from another host" in err

import subprocess
from pathlib import Path

import pytest

BANNER = "<p data-disclaimer>bwtidy is alpha software</p>"


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
    write(root, "zensical.toml", '[project]\nnav = ["index.md", "how-to/run.md"]\n')
    write(root, "docs/index.md", "# Home\n")
    write(root, "docs/how-to/run.md", "# Run\n")
    write(root, "overrides/main.html", "{% extends 'base.html' %}\n")
    monkeypatch.setattr(docs_site, "ROOT", root)
    return root


def fake_zensical(monkeypatch, pages: dict[str, str], docs_site) -> None:
    """Stand in for Zensical: write the given pages into site/ of the build directory."""

    def run(cmd, cwd):
        assert (Path(cwd) / "overrides" / "main.html").is_file()
        for name, text in pages.items():
            write(Path(cwd) / "site", name, text)
        return subprocess.CompletedProcess(cmd, 0)

    monkeypatch.setattr(docs_site.subprocess, "run", run)


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

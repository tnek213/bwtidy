import errno
import os
import re
import shutil
import socket
import struct
import subprocess
import threading
import tomllib
import zlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest
import yaml

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


def fake_zensical(
    monkeypatch, pages: dict[str, str], docs_site, keep: Path | None = None
) -> dict[str, str]:
    """Stand in for Zensical: write the given pages into site/ of the build directory.

    Returns what it saw: the restore page it was handed, under "restore.md", and the config,
    under "zensical.toml". With keep, the docs it was handed are copied there. Other commands (the
    conversion of diagrams) run as they are."""
    seen: dict[str, str] = {}
    real = subprocess.run

    def run(cmd, cwd: str | Path | None = None, **kwargs):
        if "zensical" not in cmd:
            return real(cmd, cwd=cwd, **kwargs)
        assert cwd is not None
        assert (Path(cwd) / "overrides" / "main.html").is_file()
        seen["restore.md"] = (Path(cwd) / "docs" / "restore.md").read_text()
        seen["zensical.toml"] = (Path(cwd) / "zensical.toml").read_text()
        if keep is not None:
            shutil.copytree(Path(cwd) / "docs", keep)
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


## what the stand-in for Kroki answers: black text on nothing, at 120 by 40
SVG = (
    '<svg xmlns="http://www.w3.org/2000/svg" width="120" height="40" viewBox="0 0 120 40">'
    '<text x="10" y="30" font-family="sans-serif" font-size="24" fill="#000">Kroki</text></svg>'
)
GRAPHVIZ_PAGE = '# Run\n\nThe flow:\n\n```graphviz alt="Two boxes"\ndigraph { a -> b }\n```\n'


class FakeKroki:
    """A local HTTP server that answers like Kroki: SVG for every POST, or the status set."""

    def __init__(self) -> None:
        self.requests: list[tuple[str, str]] = []
        self.status = 200
        self.answer = SVG
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:
                body = self.rfile.read(int(self.headers["Content-Length"])).decode()
                fake.requests.append((self.path, body))
                data = fake.answer.encode()
                self.send_response(fake.status)
                kind = "image/svg+xml" if fake.status == 200 else "text/plain"
                self.send_header("Content-Type", kind)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, format: str, *args) -> None:
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture
def kroki(monkeypatch, docs_site):
    fake = FakeKroki()
    monkeypatch.setenv(docs_site.KROKI_URL, fake.url)
    yield fake
    fake.close()


def pixels(path: Path) -> list[list[tuple[int, ...]]]:
    """The rows of an 8 bit RGB or RGBA PNG without interlacing, each pixel a tuple."""
    data = path.read_bytes()
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    offset, idat, header = 8, b"", b""
    while offset < len(data):
        (length,) = struct.unpack(">I", data[offset : offset + 4])
        kind = data[offset + 4 : offset + 8]
        chunk = data[offset + 8 : offset + 8 + length]
        if kind == b"IHDR":
            header = chunk
        elif kind == b"IDAT":
            idat += chunk
        offset += 12 + length
    width, height, depth, color, _, _, interlace = struct.unpack(">IIBBBBB", header)
    assert depth == 8 and color in (2, 6) and interlace == 0
    channels = 4 if color == 6 else 3
    raw = zlib.decompress(idat)
    stride = width * channels
    rows: list[bytearray] = []
    previous = bytearray(stride)
    for y in range(height):
        start = y * (stride + 1)
        kind, line = raw[start], bytearray(raw[start + 1 : start + 1 + stride])
        for i in range(stride):
            left = line[i - channels] if i >= channels else 0
            up = previous[i]
            corner = previous[i - channels] if i >= channels else 0
            if kind == 1:
                line[i] = (line[i] + left) & 0xFF
            elif kind == 2:
                line[i] = (line[i] + up) & 0xFF
            elif kind == 3:
                line[i] = (line[i] + (left + up) // 2) & 0xFF
            elif kind == 4:
                guess = left + up - corner
                pa, pb, pc = abs(guess - left), abs(guess - up), abs(guess - corner)
                best = left if pa <= pb and pa <= pc else up if pb <= pc else corner
                line[i] = (line[i] + best) & 0xFF
        rows.append(line)
        previous = line
    return [[tuple(row[x : x + channels]) for x in range(0, stride, channels)] for row in rows]


def white(pixel: tuple[int, ...]) -> bool:
    return pixel[:3] == (255, 255, 255) and (len(pixel) == 3 or pixel[3] == 255)


def build_with_diagrams(repo, monkeypatch, tmp_path, docs_site, page: str) -> Path:
    """Build with page as docs/how-to/run.md, the docs Zensical was handed in the result."""
    write(repo, "docs/how-to/run.md", page)
    kept = tmp_path / f"kept-{len(list(tmp_path.glob('kept-*')))}"
    fake_zensical(monkeypatch, {"index.html": f"<body>{BANNER}</body>"}, docs_site, keep=kept)
    assert docs_site.build(None) == 0
    return kept


def test_a_diagram_becomes_a_picture_of_a_png(repo, monkeypatch, tmp_path, kroki, docs_site):
    kept = build_with_diagrams(repo, monkeypatch, tmp_path, docs_site, GRAPHVIZ_PAGE)
    text = (kept / "how-to" / "run.md").read_text()
    match = re.search(r'!\[Two boxes\]\((diagram-[0-9a-f]+\.png)\)\{ width="(\d+)" \}', text)
    assert match, text
    assert "```" not in text and "digraph" not in text
    png = kept / "how-to" / match[1]
    rows = pixels(png)
    assert len(rows[0]) == 240 and len(rows) == 80
    assert int(match[2]) == len(rows[0]) // 2
    ## the corner is white, the text (x 10 to about 80, y 10 to 30 in the SVG) is not
    assert white(rows[0][0]) and white(rows[-1][-1])
    text_area = [pixel for row in rows[20:60] for pixel in row[20:160]]
    assert any(not white(pixel) for pixel in text_area)
    assert kroki.requests == [("/graphviz/svg", "digraph { a -> b }\n")]
    assert "```graphviz" in (repo / "docs" / "how-to" / "run.md").read_text()


def test_a_diagram_sized_in_pt_is_drawn_at_96_dpi(repo, monkeypatch, tmp_path, kroki, docs_site):
    """As Graphviz and PlantUML size their SVG, 90 by 30 pt being 120 by 40 CSS pixels."""
    kroki.answer = SVG.replace('width="120" height="40"', 'width="90pt" height="30pt"')
    kept = build_with_diagrams(repo, monkeypatch, tmp_path, docs_site, GRAPHVIZ_PAGE)
    [png] = (kept / "how-to").glob("diagram-*.png")
    rows = pixels(png)
    assert (len(rows[0]), len(rows)) == (240, 80)
    assert 'width="120"' in (kept / "how-to" / "run.md").read_text()


def test_an_svg_resvg_cannot_read_fails_the_build(repo, monkeypatch, capsys, kroki, docs_site):
    kroki.answer = "<html>not a picture</html>"
    write(repo, "docs/how-to/run.md", GRAPHVIZ_PAGE)
    fake_zensical(monkeypatch, {"index.html": f"<body>{BANNER}</body>"}, docs_site)
    assert docs_site.build(None) == 1
    err = capsys.readouterr().err
    assert "docs-site: docs/how-to/run.md:5: resvg could not convert the SVG of Kroki" in err
    assert not (repo / ".cache" / "kroki").exists() or not any(
        (repo / ".cache" / "kroki").iterdir()
    )


def converter(docs_site) -> dict[str, Any]:
    namespace: dict[str, Any] = {"__name__": "convert"}
    exec(docs_site.CONVERT, namespace)
    return namespace


def fake_render(installed: set[str]):
    """A stand-in for resvg that draws a text only in an installed family, as resvg does.

    A family list draws in its first installed family, a generic name in the font it is mapped
    to, and a list without any of them in the serif one, resvg's fallback."""

    def render(svg_string: str, **fonts: str) -> bytes:
        match = re.search(r'font-family="([^"]*)">(\w*)<', svg_string)
        assert match
        names = [name.strip().strip("'\"") for name in match[1].split(",") if name.strip()]
        generic = {"serif": "serif_family", "sans-serif": "sans_serif_family"}
        generic["monospace"] = "monospace_family"
        mapped = [fonts.get(generic[name], name) if name in generic else name for name in names]
        mapped.append(fonts.get("serif_family", "Times New Roman"))
        return b"text" if match[2] and any(name in installed for name in mapped) else b"empty"

    return render


def generic_families(docs_site, installed: set[str]) -> dict[str, str]:
    """What the converter picks with only the installed families drawing any text."""
    convert = converter(docs_site)
    return convert["generic_families"](fake_render(installed), docs_site.GENERIC_FONTS)


def test_the_converter_takes_the_first_installed_font_of_each_generic_family(docs_site):
    installed = {"DejaVu Serif", "DejaVu Sans", "Liberation Sans", "DejaVu Sans Mono"}
    assert generic_families(docs_site, installed) == {
        "serif_family": "DejaVu Serif",
        "sans_serif_family": "Liberation Sans",
        "monospace_family": "DejaVu Sans Mono",
        "font_family": "DejaVu Serif",
    }


def test_a_generic_family_without_an_installed_font_gets_a_font_no_system_has(docs_site):
    nowhere = converter(docs_site)["NOWHERE"]
    assert generic_families(docs_site, {"Arial"}) == {
        "serif_family": nowhere,
        "sans_serif_family": "Arial",
        "monospace_family": nowhere,
        "font_family": nowhere,
    }


def test_a_serif_font_does_not_stand_in_for_a_missing_sans_serif_one(docs_site):
    """resvg draws a family it cannot find in the serif font, the probe must not count that."""
    fonts = generic_families(docs_site, {"Times New Roman"})
    assert fonts["serif_family"] == "Times New Roman"
    assert fonts["sans_serif_family"] == converter(docs_site)["NOWHERE"]


def missing_font(docs_site, installed: set[str], svg: str) -> str | None:
    convert = converter(docs_site)
    render = fake_render(installed)
    fonts = convert["generic_families"](render, docs_site.GENERIC_FONTS)
    return convert["missing_font"](render, svg.encode(), fonts, docs_site.GENERIC_FONTS)


def svg_with(text: str, style: str = "") -> str:
    return f'<svg xmlns="http://www.w3.org/2000/svg"><style>{style}</style>{text}</svg>'


def test_text_in_a_generic_family_without_a_font_is_missing_one(docs_site):
    svg = svg_with('<text font-family="sans-serif">A</text>')
    assert missing_font(docs_site, {"DejaVu Serif"}, svg) == (
        "no installed font draws its sans-serif text, "
        "tried Arial, Helvetica, Liberation Sans, DejaVu Sans, Noto Sans"
    )
    assert missing_font(docs_site, {"DejaVu Sans"}, svg) is None


def test_text_without_a_family_needs_a_serif_font(docs_site):
    svg = svg_with("<text>A</text>")
    assert "draws its serif text" in (missing_font(docs_site, {"DejaVu Sans"}, svg) or "")
    assert missing_font(docs_site, {"Noto Serif"}, svg) is None


def test_a_family_of_a_style_sheet_counts_as_well(docs_site):
    """As Mermaid names its fonts, in CSS with a generic family at the end."""
    svg = svg_with("<text>A</text>", '#m{font-family:"trebuchet ms",verdana,monospace;}')
    assert "draws its monospace text" in (missing_font(docs_site, {"DejaVu Sans"}, svg) or "")
    assert missing_font(docs_site, {"verdana"}, svg) is None
    assert missing_font(docs_site, {"DejaVu Sans Mono"}, svg) is None


def test_an_svg_without_text_needs_no_font(docs_site):
    svg = svg_with('<rect width="1" height="1"/><text font-family="serif"> </text>')
    assert missing_font(docs_site, set(), svg) is None


def fitted(docs_site, svg: str) -> str:
    """The SVG as the converter hands it to resvg, with the families it names fitted."""
    fit = converter(docs_site)["with_generic_families"]
    return fit(svg.encode(), docs_site.NAMED_FONTS).decode()


## a d2 style sheet: a text class in an embedded font, and the @font-face resvg does not load
D2_STYLE = (
    '.d2-1 .text-bold { font-family: "d2-1-font-bold"; }\n'
    '@font-face { font-family: d2-1-font-bold; src: url("data:font/woff;base64,AAAA"); }\n'
    '.d2-1 .text-mono { font-family: "d2-1-font-mono-italic"; }\n'
)


def test_a_named_font_gets_the_generic_family_that_stands_in_for_it(docs_site):
    texts = [f'<text font-family="{family}">A</text>' for family in ("Arial", "helvetica")]
    texts += ['<text style="font-family: Courier; font-size: 9px">A</text>']
    svg = fitted(docs_site, svg_with("".join(texts), "#m{font-family:Times}"))
    assert 'font-family="Arial, sans-serif"' in svg
    assert 'font-family="helvetica, sans-serif"' in svg
    assert 'style="font-family: Courier, monospace; font-size: 9px"' in svg
    assert "#m{font-family: Times, serif}" in svg


def test_a_generic_family_in_another_case_is_written_as_resvg_knows_it(docs_site):
    """As Graphviz writes Helvetica, which resvg would draw in the serif font."""
    svg = fitted(docs_site, svg_with('<text font-family="Helvetica,sans-Serif">A</text>'))
    assert 'font-family="Helvetica, sans-serif"' in svg


def test_an_svg_whose_families_all_draw_as_meant_stays_as_it_is(docs_site):
    svg = svg_with(
        '<text font-family="Times,serif">A</text><text font-family="No Such Font">B</text>',
        '#m{font-family:"trebuchet ms",verdana,arial,sans-serif;}',
    )
    assert fitted(docs_site, svg) == svg


def test_a_font_d2_embeds_gets_its_generic_family_weight_and_style(docs_site):
    text = '<g class="d2-1"><text class="text-bold">A</text></g>'
    svg = fitted(docs_site, svg_with(text, D2_STYLE))
    assert '.text-bold { font-family: "d2-1-font-bold", sans-serif; font-weight: bold; }' in svg
    assert "@font-face { font-family: d2-1-font-bold; src:" in svg
    expected = '"d2-1-font-mono-italic", monospace; font-style: italic; }'
    assert f".text-mono {{ font-family: {expected}" in svg


def missing_after_fitting(docs_site, installed: set[str], svg: str) -> str | None:
    return missing_font(docs_site, installed, fitted(docs_site, svg))


def test_a_named_font_draws_in_the_stand_in_of_its_generic_family(docs_site):
    svg = svg_with('<text font-family="Helvetica">A</text>', D2_STYLE)
    assert missing_after_fitting(docs_site, {"DejaVu Sans", "DejaVu Sans Mono"}, svg) is None
    assert "draws its sans-serif text" in (missing_after_fitting(docs_site, {"Arimo"}, svg) or "")


def test_a_named_font_without_a_generic_family_has_to_be_installed(docs_site):
    """resvg would draw it in the serif font without a word."""
    svg = svg_with('<text font-family="No Such Font">A</text>')
    assert missing_after_fitting(docs_site, {"DejaVu Serif"}, svg) == (
        "no installed font draws its text in No Such Font, and it names no generic family "
        "(serif, sans-serif or monospace) to fall back on"
    )
    assert missing_after_fitting(docs_site, {"No Such Font"}, svg) is None


def test_a_font_the_svg_embeds_is_named_as_the_cause(docs_site):
    style = "@font-face{font-family:Fancy;src:url(x)} text{font-family:Fancy}"
    svg = svg_with("<text>A</text>", style)
    assert missing_after_fitting(docs_site, {"DejaVu Serif"}, svg) == (
        "no installed font draws its text in Fancy, Fancy is a font the SVG embeds and resvg "
        "does not load, and it names no generic family (serif, sans-serif or monospace) to fall "
        "back on"
    )


def label(attributes: str, style: str = "", text: str = "Label") -> str:
    """An SVG of one text in a group of the class d2-1, at 120 by 40."""
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" width="120" height="40">'
        f'<style>{style}</style><g class="d2-1"><text x="4" y="30" font-size="24" {attributes}>'
        f"{text}</text></g></svg>"
    )


def drawn(docs_site, cache: Path, svgs: dict[str, str]) -> dict[str, bytes]:
    """The PNG the script's own conversion makes of each SVG, with the system's fonts."""
    assert docs_site.convert({key: svg.encode() for key, svg in svgs.items()}, cache) == {}
    return {key: (cache / f"{key}.png").read_bytes() for key in svgs}


def test_helvetica_and_arial_are_drawn_in_a_sans_serif_font(tmp_path, docs_site):
    pngs = drawn(
        docs_site,
        tmp_path,
        {
            "helvetica": label('font-family="Helvetica,sans-Serif"'),
            "arial": label('font-family="Arial"'),
            "sans": label('font-family="sans-serif"'),
            "serif": label('font-family="serif"'),
            "empty": label('font-family="serif"', text=""),
        },
    )
    assert pngs["helvetica"] not in (pngs["serif"], pngs["empty"])
    assert pngs["arial"] == pngs["sans"]


def test_d2_labels_are_drawn_in_bold_sans_serif(tmp_path, docs_site):
    """d2 draws its labels in the bold Source Sans Pro it embeds, which resvg does not load."""
    pngs = drawn(
        docs_site,
        tmp_path,
        {
            "d2": label('class="text-bold"', D2_STYLE),
            "bold": label('font-family="sans-serif" font-weight="bold"'),
            "serif": label('font-family="serif" font-weight="bold"'),
            "empty": label('class="text-bold"', D2_STYLE, text=""),
        },
    )
    assert pngs["d2"] == pngs["bold"]
    assert pngs["d2"] not in (pngs["serif"], pngs["empty"])


def test_a_diagram_in_a_font_the_svg_embeds_alone_fails_the_build(
    repo, monkeypatch, capsys, kroki, docs_site
):
    style = "@font-face{font-family:Fancy;src:url(x)} text{font-family:Fancy}"
    kroki.answer = label("", style)
    write(repo, "docs/how-to/run.md", GRAPHVIZ_PAGE)
    fake_zensical(monkeypatch, {"index.html": f"<body>{BANNER}</body>"}, docs_site)
    assert docs_site.build(None) == 1
    err = capsys.readouterr().err
    assert (
        "docs-site: docs/how-to/run.md:5: no installed font draws its text in Fancy, "
        "Fancy is a font the SVG embeds and resvg does not load"
    ) in err


def test_the_cache_key_changes_with_the_named_fonts(monkeypatch, docs_site):
    before = docs_site.diagram_key("graphviz", "digraph { a }")
    monkeypatch.setattr(docs_site, "NAMED_FONTS", {**docs_site.NAMED_FONTS, "serif": ["Georgia"]})
    assert docs_site.diagram_key("graphviz", "digraph { a }") != before


def test_a_diagram_whose_text_no_font_draws_fails_the_build(
    repo, monkeypatch, capsys, kroki, docs_site
):
    fonts = {**docs_site.GENERIC_FONTS, "sans_serif": ["No Such Sans", "No Such Sans Either"]}
    monkeypatch.setattr(docs_site, "GENERIC_FONTS", fonts)
    write(repo, "docs/how-to/run.md", GRAPHVIZ_PAGE)
    fake_zensical(monkeypatch, {"index.html": f"<body>{BANNER}</body>"}, docs_site)
    assert docs_site.build(None) == 1
    err = capsys.readouterr().err
    assert (
        "docs-site: docs/how-to/run.md:5: no installed font draws its sans-serif text, "
        "tried No Such Sans, No Such Sans Either\n"
    ) in err
    assert not (repo / ".cache" / "kroki").exists() or not any(
        (repo / ".cache" / "kroki").iterdir()
    )


@pytest.mark.parametrize(
    ("workflow", "build"), [("ci.yml", "scripts/ci"), ("docs.yml", "scripts/docs-site build")]
)
def test_a_workflow_that_builds_docs_installs_the_font_of_the_image_first(workflow, build):
    """A GitHub runner has no font resvg finds, so without it the build fails on a diagram."""
    dockerfile = (REAL_ROOT / "Dockerfile").read_text()
    [package] = re.findall(r"--no-install-recommends (fonts-\S+)", dockerfile)
    jobs = yaml.safe_load((REAL_ROOT / ".github" / "workflows" / workflow).read_text())["jobs"]
    [steps] = [
        job["steps"]
        for job in jobs.values()
        if any(build in s.get("run", "") for s in job["steps"])
    ]
    runs = [step.get("run", "") for step in steps]
    at = next(i for i, run in enumerate(runs) if build in run)
    install = f"sudo apt-get install -y --no-install-recommends {package}"
    assert any(install in run for run in runs[:at]), runs


def test_a_diagram_in_a_list_step_keeps_its_indent(repo, monkeypatch, tmp_path, kroki, docs_site):
    page = '# Run\n\n1. Look:\n\n    ```d2 alt="A to B"\n    a -> b\n    ```\n\n2. Done.\n'
    kept = build_with_diagrams(repo, monkeypatch, tmp_path, docs_site, page)
    lines = (kept / "how-to" / "run.md").read_text().split("\n")
    assert lines[4].startswith("    ![A to B](diagram-")
    assert lines[6] == "2. Done."
    assert kroki.requests == [("/d2/svg", "a -> b\n")]


def test_a_code_block_of_another_language_stays(repo, monkeypatch, tmp_path, kroki, docs_site):
    page = "# Run\n\n```python\nprint('hi')\n```\n\n````markdown\n```mermaid\nx\n```\n````\n"
    kept = build_with_diagrams(repo, monkeypatch, tmp_path, docs_site, page)
    assert (kept / "how-to" / "run.md").read_text() == page
    assert kroki.requests == []
    assert not (repo / ".cache").exists()


def test_a_diagram_without_alt_text_fails_the_build(repo, monkeypatch, capsys, kroki, docs_site):
    write(repo, "docs/how-to/run.md", "# Run\n\n```mermaid\ngraph TD\n```\n")
    fake_zensical(monkeypatch, {"index.html": f"<body>{BANNER}</body>"}, docs_site)
    assert docs_site.build(None) == 1
    assert "docs-site: docs/how-to/run.md:3: diagram without alt text" in capsys.readouterr().err
    assert kroki.requests == []


def test_mermaid_gets_the_line_that_keeps_its_labels(repo, monkeypatch, tmp_path, kroki, docs_site):
    page = '# Run\n\n```mermaid alt="A flow"\ngraph TD\n  A --> B\n```\n'
    build_with_diagrams(repo, monkeypatch, tmp_path, docs_site, page)
    assert kroki.requests == [("/mermaid/svg", f"{docs_site.MERMAID_INIT}\ngraph TD\n  A --> B\n")]


def test_mermaid_with_its_own_init_reaches_kroki_unchanged(
    repo, monkeypatch, tmp_path, kroki, docs_site
):
    source = '%%{init: {"theme": "forest"}}%%\ngraph TD\n  A --> B\n'
    page = f'# Run\n\n```mermaid alt="A flow"\n{source}```\n'
    build_with_diagrams(repo, monkeypatch, tmp_path, docs_site, page)
    assert kroki.requests == [("/mermaid/svg", source)]


def test_a_second_build_takes_the_diagram_from_the_cache(
    repo, monkeypatch, tmp_path, kroki, docs_site
):
    first = build_with_diagrams(repo, monkeypatch, tmp_path, docs_site, GRAPHVIZ_PAGE)
    assert len(kroki.requests) == 1
    assert len(list((repo / ".cache" / "kroki").glob("*.png"))) == 1
    second = build_with_diagrams(repo, monkeypatch, tmp_path, docs_site, GRAPHVIZ_PAGE)
    assert len(kroki.requests) == 1
    assert (first / "how-to" / "run.md").read_text() == (second / "how-to" / "run.md").read_text()
    [png] = (second / "how-to").glob("diagram-*.png")
    assert png.read_bytes() == next((first / "how-to").glob("diagram-*.png")).read_bytes()


def test_a_cache_on_another_filesystem_than_the_temp_directory_gets_the_png(
    repo, monkeypatch, tmp_path, kroki, docs_site
):
    cache = repo / ".cache" / "kroki"
    real = os.replace

    def replace(src, dst) -> None:
        ## the cache stands for a filesystem of its own, a rename into or out of it fails
        if (cache in Path(src).parents) != (cache in Path(dst).parents):
            raise OSError(errno.EXDEV, "Invalid cross-device link", str(src), None, str(dst))
        real(src, dst)

    monkeypatch.setattr(docs_site.os, "replace", replace)
    kept = build_with_diagrams(repo, monkeypatch, tmp_path, docs_site, GRAPHVIZ_PAGE)
    [png] = cache.glob("*.png")
    assert png.read_bytes() == next((kept / "how-to").glob("diagram-*.png")).read_bytes()
    assert list(cache.iterdir()) == [png]


def test_kroki_answering_an_error_fails_the_build(repo, monkeypatch, capsys, kroki, docs_site):
    kroki.status = 400
    kroki.answer = "Error 400: Syntax error in graph (line: 1)"
    write(repo, "docs/how-to/run.md", GRAPHVIZ_PAGE)
    fake_zensical(monkeypatch, {"index.html": f"<body>{BANNER}</body>"}, docs_site)
    assert docs_site.build(None) == 1
    err = capsys.readouterr().err
    assert "docs-site: docs/how-to/run.md:5: Kroki could not render the graphviz diagram" in err
    assert "answered 400: Error 400: Syntax error in graph (line: 1)" in err


def test_an_unreachable_kroki_fails_the_build(repo, monkeypatch, capsys, docs_site):
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    monkeypatch.setenv(docs_site.KROKI_URL, f"http://127.0.0.1:{port}")
    write(repo, "docs/how-to/run.md", GRAPHVIZ_PAGE)
    fake_zensical(monkeypatch, {"index.html": f"<body>{BANNER}</body>"}, docs_site)
    assert docs_site.build(None) == 1
    err = capsys.readouterr().err
    assert "docs-site: docs/how-to/run.md:5: Kroki could not render the graphviz diagram" in err
    assert f"http://127.0.0.1:{port}/graphviz/svg could not be reached" in err


def test_bwtidy_kroki_url_picks_the_server(monkeypatch, docs_site):
    monkeypatch.setenv(docs_site.KROKI_URL, "https://kroki.bwtidy.example/")
    assert docs_site.kroki_url("d2") == "https://kroki.bwtidy.example/d2/svg"


@pytest.mark.parametrize("value", [None, "", " "])
def test_without_bwtidy_kroki_url_the_public_kroki_is_used(monkeypatch, docs_site, value):
    if value is None:
        monkeypatch.delenv(docs_site.KROKI_URL, raising=False)
    else:
        monkeypatch.setenv(docs_site.KROKI_URL, value)
    assert docs_site.kroki_url("graphviz") == "https://kroki.io/graphviz/svg"


def test_the_types_are_kroki_s_thirty(docs_site):
    assert len(set(docs_site.KROKI_TYPES)) == 30
    assert {"mermaid", "plantuml", "d2", "graphviz", "vegalite"} <= set(docs_site.KROKI_TYPES)

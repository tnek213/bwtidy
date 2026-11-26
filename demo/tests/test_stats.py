"""The demo's usage stats (dev/demo.md, Usage stats): what the wrapper adds to bwtidy's stats,
GET /demo/stats.json, the Usage section of the start page and the vendored uPlot.

The wrapper runs as in test_gateway.py, with the fake worker and a mock controller, and with
BWTIDY_STATS on in a file of the test's own.
"""

import contextlib
import hashlib
import json
import re
import sqlite3
from collections.abc import Generator, Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx2 as httpx
import pytest
from bwtidy.stats import db

from bwtidy_demo import stats
from bwtidy_demo.gateway import NOTES, create_demo_app
from tests.test_gateway import (
    Env,
    FakeController,
    demo_settings,
    email,
    gateway_settings,
    quick,
    tmp_path,
    wait_for,
)

## the fixtures of test_gateway.py, used here by their names
__all__ = ["quick", "tmp_path"]

ROOT = Path(__file__).resolve().parents[2]
STATIC = ROOT / "demo" / "src" / "bwtidy_demo" / "static"
VAULT_ID = "amber-paper-cloud-river"
STATUS = {"ready": 1, "taken": 1, "building": 1, "failed": 0, "max": 6}


class StatusController(FakeController):
    """The fake controller with GET /status."""

    def __init__(self, ready: list[str] | None = None) -> None:
        super().__init__(ready)
        self.status_calls = 0

    def handle(self, request: httpx.Request) -> httpx.Response:
        if request.method == "GET" and request.url.path == "/status":
            self.status_calls += 1
            return httpx.Response(200, json=STATUS)
        return super().handle(request)


@contextlib.contextmanager
def stats_env(
    tmp_path: Path, controller: FakeController | None = None, on: bool = True
) -> Generator[Env]:
    from fastapi.testclient import TestClient

    controller = controller or StatusController()
    extra = {"stats": True, "stats_db": tmp_path / "stats.db"} if on else {}
    app = create_demo_app(
        gateway_settings(tmp_path, **extra),
        demo_settings(),
        transport=httpx.MockTransport(controller.handle),
    )
    with TestClient(app) as client:
        yield Env(client, controller, tmp_path)


@pytest.fixture
def env(tmp_path: Path) -> Iterator[Env]:
    with stats_env(tmp_path) as opened:
        yield opened


def rows(env: Env, query: str) -> list[tuple]:
    conn = sqlite3.connect(env.root / "stats.db")
    try:
        return conn.execute(query).fetchall()
    finally:
        conn.close()


def flush(env: Env) -> None:
    """A capacity sample now, and everything seen written."""
    recorder = env.gateway.recorder
    env.call(recorder.sample)
    env.call(recorder.flush)


def samples(env: Env) -> list[dict[str, Any]]:
    return [json.loads(extra) for (extra,) in rows(env, "SELECT extra FROM capacity ORDER BY at")]


## --- what the wrapper records


def test_events_carry_the_vault_id_and_samples_the_demos_numbers(env: Env):
    wait_for(lambda: env.demo.counters.status is not None, "the controller's counts")
    cookies = env.start()
    assert env.request("GET", "/api/grid", cookies).status_code == 200
    flush(env)
    accounts = {account for (account,) in rows(env, "SELECT account FROM events")}
    assert accounts == {VAULT_ID}
    [extra] = samples(env)
    assert extra["sessions"] == 1
    assert extra["turned_away"] == 0
    assert extra["wait_ms_total"] == extra["wait_ms_max"] > 0
    assert {name: extra[name] for name in ("ready", "building", "max")} == {
        "ready": 1,
        "building": 1,
        "max": 6,
    }
    ## the counts start over with each sample
    flush(env)
    assert samples(env)[-1]["sessions"] == 0


def test_a_visitor_turned_away_counts_in_the_sample(tmp_path: Path):
    with stats_env(tmp_path, StatusController(ready=[])) as env:
        answer = env.request("POST", "/demo/start")
        assert answer.headers["location"] == "/demo?note=free"
        flush(env)
        [extra] = samples(env)
    assert extra["turned_away"] == 1
    assert extra["sessions"] == 0


def test_a_controller_that_does_not_answer_leaves_its_counts_out(tmp_path: Path):
    controller = FakeController()
    with stats_env(tmp_path, controller) as env:
        flush(env)
        [extra] = samples(env)
    assert "ready" not in extra
    assert "max" not in extra
    assert extra["sessions"] == 0


def test_stats_off_records_nothing_and_shows_nothing(tmp_path: Path):
    controller = StatusController()
    with stats_env(tmp_path, controller, on=False) as env:
        env.start()
        page = env.request("GET", "/demo")
        assert page.status_code == 200
        assert "Usage" not in page.text
        assert "uPlot" not in page.text
        assert env.request("GET", "/demo/stats.json").status_code == 404
    assert controller.status_calls == 0
    assert not (tmp_path / "stats.db").exists()


## --- stats.json


def test_stats_json_holds_no_vault_id_or_password(env: Env):
    cookies = env.start()
    env.request("GET", "/api/grid", cookies)
    flush(env)
    answer = env.request("GET", "/demo/stats.json")
    assert answer.status_code == 200
    usage = answer.json()
    assert usage["demo"]["vaults_started"] == 1
    assert usage["demo"]["today"]["vaults_started"] == 1
    assert usage["totals"]["ui_operations"] == 1
    for value in (VAULT_ID, "amber", email("amber"), "password-of-", "@"):
        assert value not in answer.text


def test_stats_json_and_the_page_read_the_database_once_a_minute(
    env: Env, monkeypatch: pytest.MonkeyPatch
):
    reads: list[datetime] = []
    real = stats.read_usage

    def counted(path: Path, now: datetime) -> dict[str, Any]:
        reads.append(now)
        return real(path, now)

    monkeypatch.setattr(stats, "read_usage", counted)
    clock = [1000.0]
    env.demo.usage._clock = lambda: clock[0]
    for path in ("/demo/stats.json", "/demo", "/demo/stats.json", "/demo"):
        assert env.request("GET", path).status_code == 200
    assert len(reads) == 1
    clock[0] += stats.CACHE_SECONDS - 1
    env.request("GET", "/demo/stats.json")
    assert len(reads) == 1
    clock[0] += 2
    env.request("GET", "/demo/stats.json")
    env.request("GET", "/demo")
    assert len(reads) == 2


def test_a_stats_failure_fails_no_visitor(env: Env, monkeypatch: pytest.MonkeyPatch, caplog):
    def broken(path: Path, now: datetime) -> dict[str, Any]:
        raise sqlite3.OperationalError("disk I/O error")

    monkeypatch.setattr(stats, "read_usage", broken)
    page = env.request("GET", "/demo")
    assert page.status_code == 200
    assert stats.UNAVAILABLE in page.text
    assert "Start demo" in page.text
    assert env.request("GET", "/demo/stats.json").status_code == 503
    env.start()
    assert "Reading the usage stats failed: OperationalError disk I/O error" in caplog.text


## --- the start page


def seed(env: Env) -> None:
    """A few fake sessions: two UI operations, one failed, three MCP calls, an error, and
    samples of three hours, one with a visitor turned away."""
    now = datetime.now(UTC)
    at = db.stamp(now - timedelta(minutes=5))
    events = [
        db.Event(at, "a", "ui", "GET /api/grid", True, 200, 30, "test"),
        db.Event(at, "a", "ui", "PUT /api/items/{item_id}", False, 500, 40, "test"),
        db.Event(at, "b", "mcp", "list_items", True, 200, 20, "test"),
        db.Event(at, "b", "mcp", "list_items", True, 200, 20, "test"),
        db.Event(at, "b", "mcp", "tools/list", True, 200, 5, "test"),
    ]
    errors = [
        db.ErrorSeen(
            at,
            "f" * 64,
            "KeyError",
            json.dumps([["items/edit.py", "apply", 12]]),
            "ui",
            "PUT /api/items/{item_id}",
            "test",
        )
    ]
    hours = [now - timedelta(hours=hours, minutes=1) for hours in (2, 1, 0)]
    extras = [
        {"sessions": 1, "turned_away": 0, "wait_ms_total": 12000, "wait_ms_max": 12000, "max": 6},
        {"sessions": 2, "turned_away": 3, "wait_ms_total": 30000, "wait_ms_max": 18000, "max": 6},
        {"sessions": 0, "turned_away": 0, "wait_ms_total": 0, "wait_ms_max": 0, "max": 6},
    ]
    sampled = [
        db.Sample(db.stamp(when), active, 6, 0, json.dumps(extra))
        for when, active, extra in zip(hours, (1, 6, 2), extras, strict=True)
    ]
    conn = db.open_stats(env.root / "stats.db")
    try:
        db.write(conn, events, errors, sampled)
    finally:
        conn.close()


def section(page: str) -> str:
    start = page.index('<section class="card usage"')
    return page[start : page.index("</section>", start)]


def cells(html: str, caption: str) -> list[list[str]]:
    """The body rows of the table with this caption, each cell's text without tags."""
    start = html.index(f'<caption class="sr-only">{caption}</caption>')
    body = html[html.index("<tbody>", start) : html.index("</tbody>", start)]
    return [
        [re.sub(r"<[^>]+>", "", cell) for cell in re.findall(r"<td[^>]*>(.*?)</td>", row)]
        for row in re.findall(r"<tr>(.*?)</tr>", body)
    ]


def test_the_start_page_renders_the_numbers_and_tables_without_javascript(env: Env):
    seed(env)
    page = env.request("GET", "/demo").text
    usage = section(page)
    ## everything but the chart is in the HTML: strip the scripts and the numbers stay
    without_scripts = re.sub(r"<script\b.*?</script>", "", page, flags=re.DOTALL)
    assert section(without_scripts) == usage
    assert stats.LINE in usage
    tiles = re.findall(
        r'<span class="value">(.*?)</span><span class="label">(.*?)</span>'
        r'<span class="today">(.*?)</span>',
        usage,
    )
    values = {label: (value, today) for value, label, today in tiles}
    assert values["UI operations"] == ("2", "2 today")
    assert values["MCP calls"] == ("3", "3 today")
    assert values["succeeded"] == ("80%", "80% today")
    ## the samples of the last hours may lie before midnight UTC
    assert values["demo vaults used"][0] == "3"
    assert re.fullmatch(r"[0-3] today", values["demo vaults used"][1])
    assert "Turned away because the demo was full: 3" in usage
    assert "14 s on average and 18 s at most" in usage
    assert cells(usage, "Operations by channel") == [
        ["MCP", "list_items", "2", "0"],
        ["MCP", "tools/list", "1", "0"],
        ["Web UI", "GET /api/grid", "1", "0"],
        ["Web UI", "PUT /api/items/{item_id}", "0", "1"],
    ]
    assert cells(usage, "Errors by operation") == [
        ["Web UI", "PUT /api/items/{item_id}", "KeyError", "1"]
    ]
    ## no frame, no fingerprint, no account
    assert "items/edit.py" not in page
    assert "f" * 64 not in page
    chart = cells(usage, "Sessions per hour")
    assert [row[1:] for row in chart] == [["1", "6", "0"], ["6", "6", "3"], ["2", "6", "0"]]
    ## the chart's table is collapsed, the chart itself waits for the script
    assert "<details>" in usage
    assert '<div id="usage-chart"' in usage and " hidden>" in usage
    assert '<link rel="stylesheet" href="/demo/static/uplot/uPlot.min.css" />' in page
    assert '<script src="/demo/static/uplot/uPlot.iife.min.js" defer></script>' in page


def test_an_empty_database_shows_zeros_and_says_so(env: Env):
    usage = section(env.request("GET", "/demo").text)
    assert '<span class="value">0</span><span class="label">demo vaults used</span>' in usage
    assert '<span class="value">none yet</span><span class="label">succeeded</span>' in usage
    assert "No operations yet." in usage
    assert "No errors so far." in usage
    assert "No hours sampled yet." in usage


def test_the_start_page_keeps_its_notes_with_the_usage(env: Env):
    page = env.request("GET", "/demo?note=full").text
    assert NOTES["full"] in page
    assert page.index("Start demo") < page.index('<section class="card usage"')


def test_the_share_never_rounds_a_failure_away():
    assert stats.share(0, 0) == "none yet"
    assert stats.share(4, 1) == "80%"
    assert stats.share(9999, 1) == "99.9%"
    assert stats.share(5, 0) == "100%"


## --- the static files and the vendored uPlot


def vendored() -> dict[str, str]:
    text = (STATIC / "uplot" / "VENDORED.md").read_text()
    return dict(re.findall(r"^\| `([\w.]+)` +\| `([0-9a-f]{128})` \|$", text, re.MULTILINE))


def test_the_vendored_uplot_files_match_their_sha512():
    sums = vendored()
    assert set(sums) == {"uPlot.iife.min.js", "uPlot.min.css"}
    for name, expected in sums.items():
        assert hashlib.sha512((STATIC / "uplot" / name).read_bytes()).hexdigest() == expected
    text = (STATIC / "uplot" / "VENDORED.md").read_text()
    version = re.search(r"^- Version: (1\.6\.\d+)$", text, re.MULTILINE)
    assert version
    assert f"uplot-{version[1]}.tgz" in text
    assert f"(v{version[1]})" in (STATIC / "uplot" / "uPlot.iife.min.js").read_text()[:100]


def test_the_wrapper_serves_its_static_files_and_nothing_else(env: Env):
    for name, kind in stats.STATIC.items():
        answer = env.request("GET", f"/demo/static/{name}")
        assert answer.status_code == 200, name
        assert answer.headers["content-type"] == kind
        assert answer.content == (STATIC / name).read_bytes()
    for name in ("uplot/VENDORED.md", "templates/start.html", "uplot", "x.js"):
        assert env.request("GET", f"/demo/static/{name}").status_code == 404, name


def test_the_page_loads_nothing_from_another_host(env: Env):
    seed(env)
    page = env.request("GET", "/demo").text
    assert re.findall(r'(?<![\w-])(?:src|href)="([^"]+)"', page) == [
        "/demo/static/uplot/uPlot.min.css",
        "/demo/static/uplot/uPlot.iife.min.js",
        "/demo/static/usage.js",
    ]
    script = (STATIC / "usage.js").read_text()
    assert "http" not in script.replace("https://", "")


## --- compose and the edge


def test_compose_keeps_the_stats_on_a_volume_of_their_own():
    compose = (ROOT / "compose.demo.yaml").read_text()
    assert '      BWTIDY_STATS: "1"\n' in compose
    assert "      BWTIDY_STATS_DB: /stats/stats.db\n" in compose
    assert "      - stats:/stats\n" in compose
    assert re.search(r"^volumes:\n(?:  .*\n)*  stats:\n", compose, re.MULTILINE)


def test_the_edge_keeps_no_access_log_but_its_error_log():
    config = (ROOT / "demo" / "edge" / "default.conf.template").read_text()
    assert "    access_log off;\n" in config
    assert "error_log" not in config

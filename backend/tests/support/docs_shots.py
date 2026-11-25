"""Documentation screenshots: shot(page, NAME, ...) writes NAME.png in a docs run.

scripts/docs-shots runs the docs tests (backend/tests/e2e/docs) with BWTIDY_DOCS_SHOTS=1. Without
it shot does nothing, so an e2e test may call it too. The image goes into the directory named by
BWTIDY_DOCS_SHOTS_DIR, a staging directory that scripts/docs-shots moves over docs/images after a
passing run, and into docs/images itself when the variable is not set. The frame sets what the
image shows: window (the whole viewport), band (the full width, cut above and below to the
elements in around plus the margin) or box (the elements in around plus the margin on every
side). Each highlight gets an outline drawn above the page, with an optional short label in a
badge at its top left corner, and the outlines are gone again once the shot is taken. A name is
lowercase letters, digits and hyphens, unique across the run.
"""

import json
import math
import os
import re
import time
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

if TYPE_CHECKING:
    from playwright.sync_api import Locator, Page, Route

ROOT = Path(__file__).resolve().parents[3]
## the one folder of every documentation image, the names are flat and unique across the repo
IMAGES = ROOT / "docs" / "images"
ENABLE_VAR = "BWTIDY_DOCS_SHOTS"
DIR_VAR = "BWTIDY_DOCS_SHOTS_DIR"

Frame = Literal["window", "band", "box"]
## one margin on every side, or (top, bottom) for a band
Margin = float | tuple[float, float]
Rect = dict[str, float]
type Highlight = Locator | tuple[Locator, str]

NAME = re.compile(r"[a-z0-9-]+")
## Playwright's bounding boxes are floats, an element flush with an edge can be off by a hair
EDGE_SLACK = 0.5
COLOR = "#e6007e"
OUTLINE = 3
## the room between an element and its outline
GAP = 2
OVERLAY = "data-docs-shot"
## the faint build commit in the app bar (AppBar.tsx) and the About page's version line
## (AboutPage.tsx) change with every commit, so do a test's times in a table. A hidden element
## keeps its room, but in the app bar it takes none: the build commit's width would move the
## buttons after it. The page is measured with the style in place.
HIDE = f"""() => {{
  const style = document.createElement('style')
  style.setAttribute('{OVERLAY}', '')
  style.textContent = `
    [data-shot-hide] {{ visibility: hidden !important; }}
    header [data-shot-hide] {{ display: none !important; }}
  `
  document.head.appendChild(style)
}}"""

DRAW = f"""marks => {{
  for (const mark of marks) {{
    const outline = document.createElement('div')
    outline.setAttribute('{OVERLAY}', '')
    Object.assign(outline.style, {{
      position: 'fixed', boxSizing: 'border-box', pointerEvents: 'none', zIndex: '2147483647',
      left: mark.left + 'px', top: mark.top + 'px',
      width: mark.width + 'px', height: mark.height + 'px',
      border: '{OUTLINE}px solid {COLOR}', borderRadius: '4px',
    }})
    document.body.appendChild(outline)
    if (mark.label === null) continue
    const badge = document.createElement('div')
    badge.setAttribute('{OVERLAY}', '')
    badge.textContent = mark.label
    Object.assign(badge.style, {{
      position: 'fixed', boxSizing: 'border-box', pointerEvents: 'none', zIndex: '2147483647',
      left: Math.max(0, mark.left - 9) + 'px', top: Math.max(0, mark.top - 9) + 'px',
      minWidth: '20px', height: '20px', padding: '0 6px', borderRadius: '10px',
      background: '{COLOR}', color: '#fff', font: 'bold 12px/20px sans-serif',
      textAlign: 'center', whiteSpace: 'nowrap',
    }})
    document.body.appendChild(badge)
  }}
}}"""
REMOVE = f"() => document.querySelectorAll('[{OVERLAY}]').forEach(e => e.remove())"
## a menu or popover grows in, measured before its transition ends its box is too small
SETTLE = "() => Promise.allSettled(document.getAnimations().map(a => a.finished))"

## the grid lays out its horizontal scrollbar a moment after its rows show, a shot before that
## shows one row more at the bottom
GRID_SCROLLBAR = """() => {
  const bar = document.querySelector('.ag-body-horizontal-scroll')
  return bar !== null && bar.getBoundingClientRect().height > 0
}"""

## name to the test that took it, in this run (one pytest process: scripts/e2e runs a test path
## or -k on one stack)
_taken: dict[str, str] = {}


class ShotError(Exception):
    pass


def enabled() -> bool:
    return os.environ.get(ENABLE_VAR) == "1"


def images_dir() -> Path:
    """The directory a shot is written into: the script's staging directory, else docs/images."""
    return Path(os.environ.get(DIR_VAR) or IMAGES)


def wait_for_grid(page: "Page") -> None:
    """Wait until the grid has laid out its horizontal scrollbar, for a shot of the whole grid."""
    page.wait_for_function(GRID_SCROLLBAR)


def check_name(name: str, test: str, taken: dict[str, str]) -> None:
    """Fail on a name that is not lowercase letters, digits and hyphens, or that is taken."""
    if not NAME.fullmatch(name):
        raise ShotError(f"shot name {name!r} must be lowercase letters, digits and hyphens")
    if name in taken:
        raise ShotError(f"shot name {name!r} is taken twice: by {taken[name]} and {test}")
    taken[name] = test


def check_inside(name: str, box: Mapping[str, float] | None, width: float, height: float) -> None:
    if box is None:
        raise ShotError(f"{name} is not visible")
    inside = (
        box["x"] >= -EDGE_SLACK
        and box["y"] >= -EDGE_SLACK
        and box["x"] + box["width"] <= width + EDGE_SLACK
        and box["y"] + box["height"] <= height + EDGE_SLACK
    )
    if not inside:
        raise ShotError(f"{name} is outside the viewport ({width:g} by {height:g})")


def clip(
    frame: Frame,
    boxes: Sequence[tuple[str, Mapping[str, float] | None]],
    viewport: Mapping[str, float],
    margin: Margin = 16,
) -> Rect:
    """The clip rectangle of a shot, whole pixels clamped to the viewport.

    boxes are the named elements' bounding boxes (None when hidden) by a name for the error.
    """
    width, height = viewport["width"], viewport["height"]
    for name, box in boxes:
        check_inside(name, box, width, height)
    if frame == "window":
        return {"x": 0, "y": 0, "width": width, "height": height}
    if frame not in ("band", "box"):
        raise ShotError(f"unknown frame {frame!r}, use window, band or box")
    if not boxes:
        raise ShotError(f"a {frame} needs at least one element in around")
    found = [box for _, box in boxes if box is not None]
    left = min(box["x"] for box in found)
    top = min(box["y"] for box in found)
    right = max(box["x"] + box["width"] for box in found)
    bottom = max(box["y"] + box["height"] for box in found)
    if isinstance(margin, tuple):
        if frame != "band":
            raise ShotError("only a band takes two margins (top, bottom)")
        above, below = margin
    else:
        above = below = margin
    if frame == "band":
        left, right = 0, width
    else:
        left, right = left - above, right + above
    top, bottom = top - above, bottom + below
    left, top = max(0, math.floor(left)), max(0, math.floor(top))
    right, bottom = min(width, math.ceil(right)), min(height, math.ceil(bottom))
    return {"x": left, "y": top, "width": right - left, "height": bottom - top}


def describe(locator: "Locator") -> str:
    """The locator's selector for an error message, its repr also names the page's URL."""
    found = re.search(r"selector='(.*)'>$", repr(locator))
    return found.group(1) if found else repr(locator)


def box_of(locator: "Locator") -> Rect | None:
    box = locator.bounding_box()
    if box is None:
        return None
    return {"x": box["x"], "y": box["y"], "width": box["width"], "height": box["height"]}


def current_test() -> str:
    return os.environ.get("PYTEST_CURRENT_TEST", "?").rsplit(" ", 1)[0]


def marks(
    highlight: Sequence[Highlight], viewport: Mapping[str, float]
) -> list[dict[str, float | str | None]]:
    result: list[dict[str, float | str | None]] = []
    pad = OUTLINE + GAP
    for entry in highlight:
        locator, label = entry if isinstance(entry, tuple) else (entry, None)
        box = box_of(locator)
        check_inside(describe(locator), box, viewport["width"], viewport["height"])
        assert box is not None
        result.append(
            {
                "left": box["x"] - pad,
                "top": box["y"] - pad,
                "width": box["width"] + 2 * pad,
                "height": box["height"] + 2 * pad,
                "label": label,
            }
        )
    return result


def shot(
    page: "Page",
    name: str,
    frame: Frame = "window",
    around: Sequence["Locator"] = (),
    margin: Margin = 16,
    highlight: Sequence[Highlight] = (),
) -> None:
    """Write NAME.png (see images_dir) of the page in the frame, with the highlights drawn.

    highlight is a list of a locator or (locator, label). Does nothing outside a docs run.
    """
    if not enabled():
        return
    check_name(name, current_test(), _taken)
    viewport = page.viewport_size
    if viewport is None:
        raise ShotError("a shot needs a page with a fixed viewport")
    view = {"width": viewport["width"], "height": viewport["height"]}
    directory = images_dir()
    directory.mkdir(parents=True, exist_ok=True)
    try:
        page.evaluate(HIDE)
        page.evaluate(SETTLE)
        rect = clip(frame, [(describe(loc), box_of(loc)) for loc in around], view, margin)
        drawn = marks(highlight, view)
        page.evaluate(DRAW, drawn)
        page.screenshot(
            path=directory / f"{name}.png",
            clip={"x": rect["x"], "y": rect["y"], "width": rect["width"], "height": rect["height"]},
            animations="disabled",
            caret="hide",
        )
    finally:
        page.evaluate(REMOVE)


## Times, backup names and sizes differ from run to run: a test whose shot shows them routes the
## page's API requests through a FixedValues (a hidden cell keeps its width, so the table would
## still size its column by the real text).
## the time of a change or a queue entry, and the time of a backup
CHANGE_KEYS = {"at", "createdAt", "decidedAt"}
BACKUP_KEYS = {"time"}
## a backup's size differs a little from run to run, every backup shows this one
BACKUP_SIZE = 26_800
## changes in the Change log this close in time, with no backup between them, were written by
## one commit (the queue entries of one test may be as close)
GROUP = timedelta(seconds=5)
## the room between two fixed instants
STEP = timedelta(seconds=23)
## protocol.py's BACKUP_TIME_FORMAT and BACKUP_NAME
BACKUP_FORMAT = "%Y%m%dT%H%M%SZ"
BACKUP_NAME = re.compile(r"^(\d{8}T\d{6}Z)\.zip\.age$")

## a real instant and whether it is a backup's: of a change and a backup in the same second
## (both have whole seconds), the backup was taken after the change
type Instant = tuple[datetime, bool]


def parse(text: str) -> datetime:
    return datetime.fromisoformat(text.replace("Z", "+00:00")).astimezone(UTC)


def iso(when: datetime) -> str:
    return when.isoformat().replace("+00:00", "Z")


def backup_time(name: str) -> datetime | None:
    found = BACKUP_NAME.match(name)
    if found is None:
        return None
    return datetime.strptime(found.group(1), BACKUP_FORMAT).replace(tzinfo=UTC)


class FixedValues:
    """Maps the real instants a page shows onto fixed ones, keeping their order, and every
    backup's size onto BACKUP_SIZE.

    The newest instant becomes newest, each older one STEP before the next. Changes closer
    than group, with no backup between them, share one time. Learn every instant of a page
    before it loads (prefetched through the API), so the order the page's requests answer in
    does not matter. On a fresh vault the same test shows the same newest instants in the same
    order, and an older one that comes or goes by chance (a background backup of an earlier
    login) moves no newer one.
    """

    def __init__(self, newest: datetime, group: timedelta = timedelta(0)) -> None:
        self.newest = newest
        self.group = group
        self.fixed: dict[Instant, datetime] = {}

    def learn(self, data: Any) -> None:
        found = self.instants(data)
        if found <= self.fixed.keys():
            return
        rank = -1
        last: Instant | None = None
        fixed: dict[Instant, datetime] = {}
        for instant in sorted(found | self.fixed.keys(), reverse=True):
            joins = (
                last is not None
                and not instant[1]
                and not last[1]
                and last[0] - instant[0] < self.group
            )
            if not joins:
                rank += 1
            fixed[instant] = self.newest - STEP * rank
            last = instant
        self.fixed = fixed

    def instants(self, data: Any) -> set[Instant]:
        found: set[Instant] = set()
        if isinstance(data, dict):
            for key, value in data.items():
                if key in CHANGE_KEYS | BACKUP_KEYS and isinstance(value, str):
                    found.add((parse(value), key in BACKUP_KEYS))
                else:
                    found |= self.instants(value)
        elif isinstance(data, list):
            for value in data:
                found |= self.instants(value)
        elif isinstance(data, str) and (when := backup_time(data)):
            found.add((when, True))
        return found

    def rewrite(self, data: Any) -> Any:
        if isinstance(data, dict):
            return {
                key: iso(self.fixed[(parse(value), key in BACKUP_KEYS)])
                if key in CHANGE_KEYS | BACKUP_KEYS and isinstance(value, str)
                else BACKUP_SIZE
                if key == "size" and isinstance(value, int)
                else self.rewrite(value)
                for key, value in data.items()
            }
        if isinstance(data, list):
            return [self.rewrite(value) for value in data]
        if isinstance(data, str) and (when := backup_time(data)):
            return self.fixed[(when, True)].strftime(BACKUP_FORMAT) + ".zip.age"
        return data

    def route(self, route: "Route") -> None:
        response = route.fetch()
        data = response.json()
        self.learn(data)
        route.fulfill(response=response, body=json.dumps(self.rewrite(data)))


## seconds for the backup a write starts in the background
BACKUP_TIMEOUT = 120


def wait_for_next_backup(api: Any) -> None:
    """Wait for the backup the newest write started in the background, so a Change log shot shows
    it every time. api is the e2e tests' Api."""
    newest = max(api.get("/api/change-log"), key=lambda entry: entry["id"])
    written, used = parse(newest["at"]), newest["backup"]

    def taken() -> bool:
        return any(
            backup["name"] != used and parse(backup["time"]) >= written
            for backup in api.get("/api/backups")
        )

    deadline = time.monotonic() + BACKUP_TIMEOUT
    while not taken():
        assert time.monotonic() < deadline, "no backup after the write"
        time.sleep(1)


def hide_revision_dates(page: "Page") -> None:
    """A grid shot shows revisionDate too, the date of the last seeding, which changes the image."""
    page.locator(".ag-row .ag-cell[col-id='revisionDate']").evaluate_all(
        "cells => cells.forEach(cell => cell.setAttribute('data-shot-hide', ''))"
    )

"""What a worker with the stats on reports to the gateway: its names, and an exception's type and
frames inside the package with its 500, never its text or its locals."""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from bwtidy.config import env_seconds
from bwtidy.protocol import STATS_ERROR_HEADER, STATS_NAMES_PATH
from bwtidy.stats.parse import Names, error_report
from bwtidy.stats.report import report_to_gateway

MARKER = "zq7marker"


def app() -> FastAPI:
    app = FastAPI()

    @app.get("/api/items/{item_id}")
    def item(item_id: str) -> dict[str, str]:
        return {"id": item_id}

    @app.get("/api/fail")
    def fail() -> None:
        secret = f"{MARKER} local"
        env_seconds(secret, 1.0)

    @app.post("/mcp")
    def mcp() -> None:
        return None

    async def tools() -> list[str]:
        return ["list_items", "get_item"]

    report_to_gateway(app, tools)
    return app


def test_an_unhandled_exception_answers_500_with_its_type_and_package_frames(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setenv(f"{MARKER} local", f"{MARKER} value")
    with TestClient(app(), raise_server_exceptions=False) as client:
        answer = client.get("/api/fail")
    assert answer.status_code == 500
    assert answer.text == "Internal Server Error"
    header = answer.headers[STATS_ERROR_HEADER]
    assert MARKER not in header
    report = error_report(header)
    assert report is not None
    assert report.exception_type == "bwtidy.config.SettingsError"
    assert [(path, function) for path, function, _ in report.frames] == [
        ("config.py", "env_seconds")
    ]


def test_the_names_are_the_api_routes_the_tools_and_the_mcp_methods():
    with TestClient(app()) as client:
        answer = client.get(STATS_NAMES_PATH)
    assert answer.status_code == 200
    report = answer.json()
    assert report["routes"] == [
        {"path": "/api/items/{item_id}", "methods": ["GET"]},
        {"path": "/api/fail", "methods": ["GET"]},
    ]
    assert report["tools"] == ["get_item", "list_items"]
    assert {"initialize", "tools/call", "notifications/initialized"} <= set(report["methods"])
    names = Names.from_report(report)
    assert names.ui_operation("GET", "/api/items/x") == "GET /api/items/{item_id}"

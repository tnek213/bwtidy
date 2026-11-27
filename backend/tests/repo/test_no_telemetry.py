"""bwtidy has no telemetry (docs/explanation/overview.md): no package that reports usage, errors or
traces to anyone may land in a lock file.

fastapi and mcp import opentelemetry-api, which does nothing without an SDK and an exporter, so the
API alone is allowed and every other OpenTelemetry package is not.
"""

import json
import re
import tomllib

from devlib.paths import ROOT

TELEMETRY = re.compile(
    r"opentelemetry|sentry|posthog|mixpanel|segment|datadog|ddtrace|newrelic|honeycomb|amplitude"
    r"|bugsnag|rollbar|logrocket|telemetry|analytics",
    re.IGNORECASE,
)
ALLOWED = {"opentelemetry-api"}


def python_packages() -> list[str]:
    lock = tomllib.loads((ROOT / "backend" / "uv.lock").read_text())
    return [package["name"] for package in lock["package"]]


def npm_packages(project: str) -> list[str]:
    lock = json.loads((ROOT / project / "package-lock.json").read_text())
    return [path.rsplit("node_modules/", 1)[-1] for path in lock["packages"] if path]


def telemetry(names: list[str]) -> list[str]:
    return sorted({name for name in names if TELEMETRY.search(name) and name not in ALLOWED})


def test_the_backend_lock_has_no_telemetry_package():
    assert telemetry(python_packages()) == []


def test_the_frontend_lock_has_no_telemetry_package():
    assert telemetry(npm_packages("frontend")) == []


def test_the_bitwarden_cli_lock_has_no_telemetry_package():
    assert telemetry(npm_packages("docker/bitwarden-cli")) == []


def test_the_markdownlint_lock_has_no_telemetry_package():
    assert telemetry(npm_packages("tools/markdownlint")) == []


def test_the_check_finds_a_telemetry_package():
    names = ["fastapi", "opentelemetry-api", "opentelemetry-sdk", "@sentry/react"]
    assert telemetry(names) == ["@sentry/react", "opentelemetry-sdk"]

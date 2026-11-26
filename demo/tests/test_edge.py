"""The edge's config (demo/edge): the trusted proxies, the port and the compose override."""

import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
TEMPLATE = ROOT / "demo" / "edge" / "default.conf.template"
SCRIPT = ROOT / "demo" / "edge" / "15-trusted-proxies.sh"
OVERRIDE = Path(__file__).parent / "files" / "sidecar-override.yaml"

DEFAULTS = {
    "DEMO_START_RATE": "6r/m",
    "DEMO_START_BURST": "5",
    "DEMO_MCP_RATE": "10r/s",
    "DEMO_MCP_BURST": "40",
    "DEMO_EDGE_PORT": "80",
    "DEMO_TRUSTED_PROXIES": "",
}

needs_envsubst = pytest.mark.skipif(shutil.which("envsubst") is None, reason="no envsubst")


def render(tmp_path: Path, **env: str) -> tuple[str, str]:
    """Return the server config and the real-ip config as the edge writes them at start."""
    values = {**DEFAULTS, **env}
    # the image's envsubst step substitutes only the defined DEMO_ variables
    names = " ".join(f"${{{name}}}" for name in values)
    config = subprocess.run(
        ["envsubst", names],
        input=TEMPLATE.read_text(),
        env={**os.environ, **values},
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    subprocess.run(
        ["sh", str(SCRIPT)],
        env={**os.environ, **values, "NGINX_CONF_DIR": str(tmp_path)},
        check=True,
    )
    real_ip = tmp_path / "00-real-ip.conf"
    return config, real_ip.read_text() if real_ip.exists() else ""


@needs_envsubst
def test_an_empty_list_trusts_no_header(tmp_path: Path):
    config, real_ip = render(tmp_path)
    assert real_ip == ""
    assert "set_real_ip_from" not in config
    assert "real_ip_header" not in config
    assert "listen 80 default_server;" in config
    assert not re.search(r"\$\{DEMO_[A-Z_]+\}", config)


@needs_envsubst
def test_a_list_trusts_each_proxy_and_the_header(tmp_path: Path):
    _, real_ip = render(tmp_path, DEMO_TRUSTED_PROXIES="100.64.0.0/10 192.0.2.7")
    assert real_ip.splitlines() == [
        "set_real_ip_from 100.64.0.0/10;",
        "set_real_ip_from 192.0.2.7;",
        "real_ip_header X-Forwarded-For;",
        "real_ip_recursive on;",
    ]


@needs_envsubst
def test_a_list_that_is_emptied_removes_the_old_file(tmp_path: Path):
    render(tmp_path, DEMO_TRUSTED_PROXIES="192.0.2.7")
    _, real_ip = render(tmp_path)
    assert real_ip == ""


@needs_envsubst
def test_the_port_is_a_setting(tmp_path: Path):
    config, _ = render(tmp_path, DEMO_EDGE_PORT="8080")
    assert "listen 8080 default_server;" in config
    assert "listen 80 " not in config


@pytest.mark.skipif(shutil.which("docker") is None, reason="no docker")
def test_the_sidecar_override_is_valid_compose():
    result = subprocess.run(
        [
            "docker",
            "compose",
            "-f",
            str(ROOT / "compose.demo.yaml"),
            "-f",
            str(OVERRIDE),
            "config",
            "--quiet",
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr

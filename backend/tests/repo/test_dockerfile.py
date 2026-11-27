"""The Dockerfile's app stage gives the commands the docs run in the container."""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
DOCKERFILE = (ROOT / "Dockerfile").read_text()
VENV_BIN = "/app/backend/.venv/bin"


def stage(name: str) -> str:
    """The lines of one stage, from its FROM line to the next."""
    match = re.search(rf"^FROM \S+ AS {name}\n(.*?)(?=^FROM |\Z)", DOCKERFILE, re.M | re.S)
    assert match, name
    return match[1]


def test_the_app_stage_puts_bwtidy_on_path():
    ## `docker compose exec bwtidy bwtidy stats` (docs/reference/configuration.md, Usage stats)
    app = stage("app")
    assert f"ln -s {VENV_BIN}/bwtidy /usr/local/bin/bwtidy" in app
    ## the script the link points at
    assert '\nbwtidy = "bwtidy.main:run"\n' in (ROOT / "backend" / "pyproject.toml").read_text()


def test_the_venv_stays_off_path_so_python3_is_the_images_own():
    ## the health check of compose.yaml runs python3
    assert not re.search(r"\bPATH=", stage("app"))
    assert "- python3\n" in (ROOT / "compose.yaml").read_text()

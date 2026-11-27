"""The image's bw is Bitwarden's open source build, from the source docker/bitwarden-cli pins.

The Dockerfile's stage bitwarden-cli-source builds it and fails on the same pattern. The dev
stack mounts docker/bitwarden-cli at /src/docker/bitwarden-cli, the worktree's pins.
"""

import json
import re
from pathlib import Path

import pytest
from support.cli import BwCli

pytestmark = pytest.mark.integration

CLI = Path("/usr/local/lib/bitwarden-cli/node_modules/@bitwarden/cli")
PINS = Path("/src/docker/bitwarden-cli")
## a module of bitwarden_license/bit-cli or bit-common. The bundled package.json names
## bitwarden_license/bit-cli/webpack.config.js in a script, a string that is no code.
LICENSED = re.compile(r"bitwarden_license/bit-c[a-z]*/src/")


def test_the_build_bundles_no_code_under_the_bitwarden_license(bw_cli: BwCli):
    bundles = sorted((CLI / "build").glob("*.js"))
    assert CLI / "build" / "bw.js" in bundles
    for bundle in bundles:
        assert LICENSED.findall(bundle.read_text()) == [], bundle.name


def test_bw_is_the_version_package_json_pins(bw_cli: BwCli):
    package = json.loads((PINS / "package.json").read_text())
    assert bw_cli.run("--version").strip() == package["dependencies"]["@bitwarden/cli"]


def test_the_image_names_the_source_of_the_build(bw_cli: BwCli):
    source = json.loads((PINS / "source.json").read_text())
    line = (CLI / "SOURCE").read_text()
    assert line.count("\n") == 1
    for value in (source["repository"], source["tag"], source["commit"]):
        assert value in line
    assert "GNU GENERAL PUBLIC LICENSE" in (CLI / "LICENSE_GPL.txt").read_text()

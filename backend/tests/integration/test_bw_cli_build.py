"""The image's bw is Bitwarden's open source build, from the source docker/bitwarden-cli pins.

The Dockerfile's stage bitwarden-cli-source builds it and fails on the same pattern. The dev
stack mounts docker/bitwarden-cli at /src/docker/bitwarden-cli, the worktree's pins.
"""

import hashlib
import json
import re
from pathlib import Path

import pytest
from support.cli import BwCli

pytestmark = pytest.mark.integration

CLI = Path("/usr/local/lib/bitwarden-cli/node_modules/@bitwarden/cli")
## what the stage bitwarden-cli-source keeps of @bitwarden/sdk-internal, which the build bundles
SDK = CLI / "sdk-internal"
SDK_REPOSITORY = "https://github.com/bitwarden/sdk-internal"
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
    lines = (CLI / "SOURCE").read_text().splitlines()
    assert len(lines) == 2
    for value in (source["repository"], source["tag"], source["commit"]):
        assert value in lines[0]
    assert "GNU GENERAL PUBLIC LICENSE" in (CLI / "LICENSE_GPL.txt").read_text()


def test_the_image_names_the_source_of_the_bundled_sdk(bw_cli: BwCli):
    package = json.loads((SDK / "package.json").read_text())
    assert package["name"] == "@bitwarden/sdk-internal"
    assert package["license"] == "GPL-3.0"
    commit = (SDK / "VERSION").read_text().strip()
    assert re.fullmatch(r"[0-9a-f]{40}", commit)
    line = (CLI / "SOURCE").read_text().splitlines()[1]
    for value in (SDK_REPOSITORY, commit, package["version"]):
        assert value in line
    assert "GNU GENERAL PUBLIC LICENSE" in (SDK / "LICENSE").read_text()


def test_every_bundled_webassembly_file_is_the_sdk_s(bw_cli: BwCli):
    """A WebAssembly file from another package would ship without SOURCE naming its source."""
    sdk = {line.split()[0] for line in (SDK / "wasm.sha256").read_text().splitlines()}
    bundled = sorted((CLI / "build").glob("*.wasm"))
    assert bundled
    for wasm in bundled:
        assert hashlib.sha256(wasm.read_bytes()).hexdigest() in sdk, wasm.name

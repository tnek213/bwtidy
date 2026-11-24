"""The version as semver.org spells it, stdlib only so scripts/devlib can load it by path."""

import re

## the pre-release letters of a Python version and semver's spelling of them
PRE_RELEASES = {"a": "alpha", "b": "beta", "rc": "rc"}
PYTHON_VERSION = re.compile(r"(\d+\.\d+\.\d+)(?:(a|b|rc)(\d+))?")


def semver(version: str) -> str:
    """The version as semver.org spells it: 0.1.0a1 is 0.1.0-alpha.1, b is beta, rc is rc."""
    match = PYTHON_VERSION.fullmatch(version)
    if match is None:
        raise ValueError(
            f"cannot spell {version!r} as semver, use X.Y.Z, optionally a, b or rc plus N"
        )
    release, letter, number = match.groups()
    return release if letter is None else f"{release}-{PRE_RELEASES[letter]}.{number}"

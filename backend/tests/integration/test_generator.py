"""bw serve of the dev stack generates what the generator options ask for.

Nothing here prints a value: each is judged into a bool before the assert. Every set of options
goes through POST /api/generator/example a few times, since a single value could pass by luck.
"""

import string
from typing import Any

import pytest
from support.app import App
from support.cli import BwCli

pytestmark = pytest.mark.integration

TRIES = 5
SPECIAL = set("!@#$%^&*")
## what bw leaves out with --ambiguous
AMBIGUOUS = set("IOl01")
## no "-": a few words of bw's list hold a hyphen of their own
SEPARATORS = ["_", " ", "", "+", "&"]


@pytest.fixture(scope="module")
def logged_in(app: App, bw_cli: BwCli) -> App:
    assert app.login().status_code == 200
    app.settle()
    return app


def example(app: App, options: dict[str, Any]) -> str:
    response = app.client.post("/api/generator/example", json=options)
    assert response.status_code == 200, f"status {response.status_code}"
    return response.json()["value"]


def fits_password(value: str, options: dict[str, Any]) -> bool:
    chars = set(value)
    classes = [
        ("uppercase", set(string.ascii_uppercase)),
        ("lowercase", set(string.ascii_lowercase)),
        ("number", set(string.digits)),
        ("special", SPECIAL),
    ]
    numbers = sum(c in string.digits for c in value)
    specials = sum(c in SPECIAL for c in value)
    allowed = set().union(*(group for name, group in classes if options[name]))
    return (
        len(value) == options["length"]
        and chars <= allowed
        and all(chars & group for name, group in classes if options[name])
        and numbers >= (options["minNumber"] if options["number"] else 0)
        and specials >= (options["minSpecial"] if options["special"] else 0)
        and not (options["ambiguous"] and chars & AMBIGUOUS)
    )


def password(**options: Any) -> dict[str, Any]:
    base = {
        "type": "password",
        "length": 24,
        "uppercase": True,
        "lowercase": True,
        "number": True,
        "special": True,
        "minNumber": 1,
        "minSpecial": 1,
        "ambiguous": False,
    }
    return {**base, **options}


@pytest.mark.parametrize(
    "options",
    [
        password(),
        password(length=5),
        password(length=128),
        password(length=12, special=False),
        password(length=16, uppercase=False, number=False, special=False),
        password(length=20, minNumber=9, minSpecial=9),
        password(length=64, ambiguous=True),
    ],
    ids=["default", "shortest", "longest", "no-special", "lowercase-only", "minimums", "ambiguous"],
)
def test_a_password_matches_its_options(logged_in: App, options: dict[str, Any]):
    fits = [fits_password(example(logged_in, options), options) for _ in range(TRIES)]
    assert all(fits)


def test_the_values_differ(logged_in: App):
    values = {example(logged_in, password()) for _ in range(TRIES)}
    assert len(values) == TRIES


@pytest.mark.parametrize("words", [3, 4, 20])
@pytest.mark.parametrize(
    "separator", SEPARATORS, ids=["underscore", "space", "none", "plus", "amp"]
)
def test_a_passphrase_matches_its_options(logged_in: App, words: int, separator: str):
    options = {
        "type": "passphrase",
        "words": words,
        "separator": separator,
        "capitalize": False,
        "includeNumber": False,
    }
    for _ in range(TRIES):
        value = example(logged_in, options)
        if separator:
            parts = value.split(separator)
            fits = len(parts) == words and all(p.replace("-", "").isalpha() for p in parts)
            fits = fits and value == value.lower()
        else:
            fits = value.replace("-", "").isalpha() and value == value.lower()
            ## the shortest word of bw's list has three letters
            fits = fits and len(value) >= 3 * words
        assert fits


def test_capitalize_and_include_number(logged_in: App):
    options = {
        "type": "passphrase",
        "words": 4,
        "separator": "_",
        "capitalize": True,
        "includeNumber": True,
    }
    for _ in range(TRIES):
        parts = example(logged_in, options).split("_")
        with_digit = [p for p in parts if any(c.isdigit() for c in p)]
        fits = len(parts) == 4 and all(p[0].isupper() for p in parts) and len(with_digit) == 1
        assert fits

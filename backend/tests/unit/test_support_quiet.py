import os
import subprocess
import sys
from pathlib import Path

import pytest

from tests.support.quiet import assert_absent, assert_present

VALUE = "value-that-must-not-be-printed-7f3a"
OTHER = "console-text-that-must-not-be-printed-2b9e"


def test_assert_absent_passes_when_the_value_is_not_there():
    assert_absent(VALUE, "nothing to see", "the text")
    assert_absent(VALUE.encode(), b"nothing to see", "the bytes")


def test_assert_absent_fails_without_the_value_or_the_text():
    text = f"before {VALUE} after"
    with pytest.raises(AssertionError) as raised:
        assert_absent(VALUE, text, "the response")
    assert str(raised.value) == "the response holds the value"
    assert VALUE not in repr(raised.value)
    assert "before" not in str(raised.value)


def test_assert_absent_fails_the_same_way_for_bytes():
    with pytest.raises(AssertionError) as raised:
        assert_absent(VALUE.encode(), VALUE.encode() + b" tail", "the file")
    assert str(raised.value) == "the file holds the value"


def test_assert_present_passes_when_the_value_is_there():
    assert_present(VALUE, f"a {VALUE} b", "the text")


def test_assert_present_fails_without_the_value_or_the_text():
    text = "the console with something else"
    with pytest.raises(AssertionError) as raised:
        assert_present(VALUE, text, "the console")
    assert str(raised.value) == "the console lacks the value"
    assert VALUE not in str(raised.value)
    assert "something else" not in str(raised.value)


@pytest.mark.parametrize("style", ["long", "short", "auto", "native"])
def test_a_failed_assert_shows_neither_the_value_nor_the_text_in_pytest_output(
    tmp_path: Path, style: str
):
    (tmp_path / "test_leak.py").write_text(
        "from tests.support.quiet import assert_absent, assert_present\n"
        f"VALUE = {VALUE!r}\n"
        f"OTHER = {OTHER!r}\n"
        "def test_absent():\n"
        "    assert_absent(VALUE, OTHER + VALUE, 'the text')\n"
        "def test_present():\n"
        "    assert_present(VALUE, OTHER, 'the text')\n"
    )
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "-p", "no:cacheprovider", f"--tb={style}", "test_leak.py"],
        cwd=tmp_path,
        env={**os.environ, "PYTHONPATH": str(Path(__file__).parents[2])},
        capture_output=True,
        text=True,
    )
    output = result.stdout + result.stderr
    assert "2 failed" in output
    assert "the text holds the value" in output
    assert "the text lacks the value" in output
    assert_absent(VALUE, output, "pytest's output")
    assert_absent(OTHER, output, "pytest's output")

import pytest


@pytest.fixture
def ci(load_script):
    return load_script("ci")


def test_the_quick_checks_leave_out_exactly_pytest_and_npm_test(ci):
    skipped = {c.label for c in ci.CHECKS} - {c.label for c in ci.quick_checks(True)}
    assert skipped == {"pytest", "npm test"}


def test_the_full_checks_are_all_of_them(ci):
    assert ci.quick_checks(False) == ci.CHECKS


@pytest.mark.parametrize("args", [["--bad"], ["--quick", "--quick"], ["--fix", "x"]])
def test_a_bad_argument_prints_the_usage_and_returns_2(ci, capsys, args):
    assert ci.main(args) == 2
    assert "usage: ci [--fix] [--quick]" in capsys.readouterr().err

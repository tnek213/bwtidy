import pytest


@pytest.fixture
def ci(load_script):
    return load_script("ci")


def test_the_quick_checks_leave_out_exactly_the_test_suites(ci):
    skipped = {c.label for c in ci.CHECKS} - {c.label for c in ci.quick_checks(True)}
    assert skipped == {"pytest", "demo pytest", "npm test"}


def test_the_full_checks_are_all_of_them(ci):
    assert ci.quick_checks(False) == ci.CHECKS


@pytest.mark.parametrize("args", [["--bad"], ["--quick", "--quick"], ["--fix", "x"]])
def test_a_bad_argument_prints_the_usage_and_returns_2(ci, capsys, args):
    assert ci.main(args) == 2
    assert "usage: ci [--fix] [--quick]" in capsys.readouterr().err


def test_markdownlint_runs_early_in_the_quick_checks(ci):
    labels = [c.label for c in ci.quick_checks(True)]
    assert "markdownlint" in labels
    assert labels.index("markdownlint") < labels.index("docs-lint")
    assert all(c.cwd == ci.ROOT for c in ci.CHECKS[: labels.index("markdownlint") + 1])


def test_fix_runs_markdownlint_fix_before_the_first_check(ci, monkeypatch):
    order = []
    fix = ci.Check("markdownlint --fix", ci.ROOT, lambda: order.append("fix"))
    first = ci.Check("first", ci.ROOT, lambda: order.append("check"))
    monkeypatch.setattr(ci, "MARKDOWN_FIX", fix)
    monkeypatch.setattr(ci, "CHECKS", [first])
    assert ci.main(["--fix"]) == 0
    assert order == ["fix", "check"]


def test_without_fix_markdownlint_fixes_nothing(ci, monkeypatch):
    order = []
    fix = ci.Check("markdownlint --fix", ci.ROOT, lambda: order.append("fix"))
    first = ci.Check("first", ci.ROOT, lambda: order.append("check"))
    monkeypatch.setattr(ci, "MARKDOWN_FIX", fix)
    monkeypatch.setattr(ci, "CHECKS", [first])
    assert ci.main([]) == 0
    assert order == ["check"]


def test_a_markdownlint_finding_fails_ci_with_its_file_line_and_rule(ci, monkeypatch, capsys):
    findings = "docs/bad.md:5:1 error MD004/ul-style Unordered list style"
    check = ci.Check("markdownlint", ci.ROOT, lambda: findings)
    monkeypatch.setattr(ci, "CHECKS", [check])
    assert ci.main([]) == 1
    assert f"FAILED: markdownlint: {findings}" in capsys.readouterr().err

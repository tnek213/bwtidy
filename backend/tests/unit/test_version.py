import pytest

from bwtidy.version import semver


@pytest.mark.parametrize(
    ("python", "expected"),
    [
        ("0.1.0a1", "0.1.0-alpha.1"),
        ("0.2.0b3", "0.2.0-beta.3"),
        ("1.0.0rc2", "1.0.0-rc.2"),
        ("1.2.3", "1.2.3"),
    ],
)
def test_a_python_version_has_semvers_spelling(python, expected):
    assert semver(python) == expected


@pytest.mark.parametrize("python", ["1.0", "1.0.0.dev1", "1.0.0post1", "v1.0.0", "1.0.0a"])
def test_a_version_semver_cannot_spell_is_refused(python):
    with pytest.raises(ValueError, match="cannot spell"):
        semver(python)

"""The near-duplicate hints of items/normalize.py: the field columns whose names differ only in case
and spaces."""

from bwtidy.items.normalize import near_duplicate_groups


def test_near_duplicate_grouping():
    columns = [
        "name",
        "login.username",
        "fields.Encryption passphrase ",
        "fields.encryption passphrase",
        "fields.WPA key",
        "fields.WPA key#2",
        "fields.User",
        "fields.user",
        "fields.username",
        "fields. USER",
    ]
    assert near_duplicate_groups(columns) == [
        {
            "key": "encryption passphrase",
            "columns": ["fields.encryption passphrase", "fields.Encryption passphrase "],
        },
        {"key": "user", "columns": ["fields. USER", "fields.User", "fields.user"]},
    ]

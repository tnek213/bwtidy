"""The item data the normalize and queue tests share."""

from collections.abc import Iterator
from pathlib import Path
from typing import Any

from tests.support.app_env import AppEnv, logged_in
from tests.support.sample_vault import SECRETS

## a login with the user in a custom field and an empty login.username
LOGIN_2: dict[str, Any] = {
    "object": "item",
    "id": "login-2",
    "type": 1,
    "name": "Example Shop",
    "fields": [
        {"name": "user", "value": "bob", "type": 0, "linkedId": None},
        {"name": "Password", "value": "shop-secret-31", "type": 1, "linkedId": None},
        {"name": "Shoe size", "value": "44", "type": 0, "linkedId": None},
    ],
    "login": {"uris": [], "username": None, "password": None, "totp": None},
    "revisionDate": "2026-10-26T15:00:00.000Z",
}
## a computer stored as a note, with the user under both spellings
NOTE_3: dict[str, Any] = {
    "object": "item",
    "id": "note-3",
    "type": 2,
    "name": "Media PC",
    "fields": [
        {"name": "User", "value": "carol", "type": 0, "linkedId": None},
        {"name": "user", "value": "carol2", "type": 0, "linkedId": None},
    ],
    "secureNote": {"type": 0},
    "revisionDate": "2026-10-26T15:01:00.000Z",
}
## both spellings of the password already, renaming one onto the other is a merge
NOTE_4: dict[str, Any] = {
    "object": "item",
    "id": "note-4",
    "type": 2,
    "name": "Camera NVR",
    "fields": [
        {"name": "Password", "value": "nvr-secret-1", "type": 1, "linkedId": None},
        {"name": "password", "value": "nvr-secret-2", "type": 1, "linkedId": None},
    ],
    "secureNote": {"type": 0},
    "revisionDate": "2026-10-26T15:02:00.000Z",
}
NOTE_5: dict[str, Any] = {
    "object": "item",
    "id": "note-5",
    "type": 2,
    "name": "Example Mail recovery codes",
    "notes": "Printed in the drawer",
    "fields": [
        {"name": "encryption passphrase", "value": "phrase-secret-5", "type": 1},
        {"name": "Recovery code", "value": "recovery-secret-6", "type": 1},
    ],
    "secureNote": {"type": 0},
    "revisionDate": "2026-10-26T15:03:00.000Z",
}
EXTRA = [LOGIN_2, NOTE_3, NOTE_4, NOTE_5]
ALL_SECRETS = [
    *SECRETS,
    "shop-secret-31",
    "nvr-secret-1",
    "nvr-secret-2",
    "phrase-secret-5",
    "recovery-secret-6",
]


def listed(env: AppEnv, *item_ids: str) -> list[dict[str, str]]:
    revisions = env.revisions()
    return [{"itemId": i, "revisionDate": revisions[i]} for i in item_ids]


def rename(env: AppEnv, old: str, new: str, *item_ids: str) -> dict[str, Any]:
    return {"kind": "renameField", "from": old, "to": new, "items": listed(env, *item_ids)}


def merge(env: AppEnv, sources: list[str], target: str, *item_ids: str, **extra: Any):
    items = listed(env, *item_ids)
    return {"kind": "mergeColumns", "sources": sources, "target": target, "items": items, **extra}


def merge_items(
    env: AppEnv, source: str, target: str, mapping: dict[str, str], **extra: Any
) -> dict[str, Any]:
    revisions = env.revisions()
    return {
        "kind": "mergeItems",
        "fromItemId": source,
        "fromRevisionDate": revisions[source],
        "toItemId": target,
        "toRevisionDate": revisions[target],
        "mapping": mapping,
        **extra,
    }


## login-2 with one uri login-1 has too (another match setting) and one of its own
SHOP_URIS = [
    {"match": 1, "uri": "https://mail.example.test"},
    {"match": 4, "uri": "https://shop.example.test"},
]


def with_shop_uris(tmp_path: Path) -> Iterator[AppEnv]:
    """The app logged in on the vault with EXTRA, login-2's uris SHOP_URIS."""
    shop = {**LOGIN_2, "login": {**LOGIN_2["login"], "uris": SHOP_URIS}}
    yield from logged_in(tmp_path, extra=[shop, *EXTRA[1:]])

"""The items of the dev vault's fixture files and the secret values in them."""

import json
from pathlib import Path
from typing import Any

DEV = Path(__file__).resolve().parents[4] / "dev"

## shorter secret values (a PIN, a card code) turn up by chance in dates and counts
MIN_SECRET_LENGTH = 6


def secret_values(item: dict[str, Any]) -> dict[str, str]:
    """The item's secret values by coordinate (see Secret values in docs/spec.md)."""
    found = {}
    for leaf in ("login.password", "login.totp", "card.number", "card.code", "sshKey.privateKey"):
        group, key = leaf.split(".")
        value = (item.get(group) or {}).get(key)
        if value:
            found[leaf] = value
    for field in item.get("fields") or []:
        if field.get("type") == 1 and field.get("value"):
            found[f"fields.{field['name']}"] = field["value"]
    return found


def load_fixture_items() -> dict[str, dict[str, Any]]:
    """The items of the fixture files in dev/ by name (unique)."""
    items = {}
    for name in ("fixture-personal.json", "fixture-org.json", "fixture-org-club.json"):
        for item in json.loads((DEV / name).read_text())["items"]:
            items[item["name"]] = item
    return items


def secrets_of(items: dict[str, dict[str, Any]]) -> list[tuple[str, str, str]]:
    """Every secret value long enough to tell from chance, as (item name, column, value)."""
    return [
        (name, column, value)
        for name, item in items.items()
        for column, value in secret_values(item).items()
        if len(value) >= MIN_SECRET_LENGTH
    ]

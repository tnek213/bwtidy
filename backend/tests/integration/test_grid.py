"""The grid API on the seeded dev vault, through a real bw serve.

Secrets are read from the app's own vault cache, because a `bw unlock` by the CLI would
invalidate the session key of the running app.
"""

import json
import secrets
from collections.abc import Iterator
from dataclasses import replace
from typing import Any

import pytest
from fastapi.testclient import TestClient
from support.app import purge_tool_notes
from support.cli import PASSWORD, BwCli

from bwtidy.config import Settings
from bwtidy.main import create_app
from bwtidy.session import SessionManager

pytestmark = pytest.mark.integration


class Grid:
    def __init__(self, client: TestClient, session: SessionManager, data: dict[str, Any]) -> None:
        self.client = client
        self.session = session
        self.data = data
        self.text = json.dumps(data)

    @property
    def column_ids(self) -> list[str]:
        return [c["id"] for c in self.data["columns"]]

    def row(self, name: str) -> dict[str, Any]:
        [row] = [r for r in self.data["rows"] if r["cells"].get("name") == name]
        return row


@pytest.fixture(scope="module")
def grid(bw_cli: BwCli, tmp_path_factory: pytest.TempPathFactory) -> Iterator[Grid]:
    purge_tool_notes(bw_cli)
    data_dir = tmp_path_factory.mktemp("data")
    settings = replace(
        Settings.from_env(), data_dir=data_dir, backup_dir=data_dir / "backups", static_dir=None
    )
    app = create_app(settings)
    try:
        with TestClient(app) as client:
            assert client.post("/api/session", json={"password": PASSWORD}).status_code == 200
            response = client.get("/api/grid")
            assert response.status_code == 200
            yield Grid(client, app.state.bw.session, response.json())
    finally:
        purge_tool_notes(bw_cli)


def secret_values(items: list[dict[str, Any]]) -> list[str]:
    """Every secret in the vault, and what is never a leaf (password history, passkey keys)."""
    found: list[Any] = [PASSWORD]
    for item in items:
        login = item.get("login") or {}
        card = item.get("card") or {}
        ssh = item.get("sshKey") or {}
        found += [login.get("password"), login.get("totp"), card.get("number"), card.get("code")]
        found.append(ssh.get("privateKey"))
        found += [f.get("value") for f in item.get("fields") or [] if f.get("type") == 1]
        found += [p.get("password") for p in item.get("passwordHistory") or []]
        found += [c.get("keyValue") for c in login.get("fido2Credentials") or []]
    return [s for s in found if s]


def leaks(value: str, text: str) -> bool:
    """A short value like a card code may occur inside an id, so only long ones match inside."""
    return json.dumps(value) in text or (len(value) >= 8 and value in text)


def test_grid_has_the_fixture_field_columns(grid: Grid):
    ids = grid.column_ids
    for column in (
        "fields.BIOS Password",
        "fields.Encryption passphrase ",
        "fields.LUKS/dm-crypt and Bitlocker",
        "fields.WPA key",
        "fields.WPA key#2",
        "fields.backup-password",
    ):
        assert column in ids
    assert "fields.Encryption passphrase" not in ids
    by_id = {c["id"]: c for c in grid.data["columns"]}
    assert by_id["fields.BIOS Password"]["secret"]
    assert not by_id["fields.MSDM Windows Key"]["secret"]
    assert by_id["login.uris"]["multi"]
    assert by_id["collections"]["multi"]


def test_grid_rows_match_the_seeded_vault(grid: Grid):
    ## 67 personal items, 10 Family items, 1 Club item and the tool note
    assert len(grid.data["rows"]) == 79
    thinkpad = grid.row("ThinkPad X1")["cells"]
    assert thinkpad["folder"] == "Infra"
    assert thinkpad["fields.BIOS Password"] == {"masked": True}
    assert thinkpad["attachments"] == ["thinkpad-recovery.txt"]
    assert grid.row("Example Forge")["cells"]["passkeys"] == 1
    laptop = grid.row("Youth laptop")["cells"]
    assert laptop["organization"] == "Family"
    assert laptop["collections"] == ["Youth"]
    assert grid.row("Visa debit")["cells"]["card.number"] == {"masked": True}
    assert grid.row("Dev Identity")["cells"]["identity.firstName"] == "Dana"
    key = grid.row("Build server key")["cells"]
    assert key["sshKey.privateKey"] == {"masked": True}
    assert key["sshKey.keyFingerprint"].startswith("SHA256:")
    assert grid.row("Dashboards")["cells"]["favorite"] is True


def test_no_secret_value_in_the_grid(grid: Grid):
    values = secret_values(grid.session.cache.items)
    assert len(values) > 50
    leaked = [i for i, s in enumerate(values) if leaks(s, grid.text)]
    ## indexes only, so a failure never prints a secret
    assert leaked == []


def test_secret_route_reveals_one_value(grid: Grid):
    row = grid.row("Backup disk")
    response = grid.client.get(
        f"/api/items/{row['id']}/secret", params={"column": "fields.Encryption passphrase "}
    )
    assert response.status_code == 200
    item = next(i for i in grid.session.cache.items if i["id"] == row["id"])
    [field] = item["fields"]
    revealed = secrets.compare_digest(response.json()["value"].encode(), field["value"].encode())
    assert revealed, "the revealed value is not the stored one"
    empty = grid.client.get(f"/api/items/{row['id']}/secret", params={"column": "login.password"})
    assert empty.status_code == 404

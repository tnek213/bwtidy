"""The gateway's accounts (docs/spec.md, Gateway): the server and email checks, the account
directories, their owners and the files the gateway reads in them."""

import logging
import os
import shutil
import signal
from pathlib import Path

import pytest

from bwtidy.gateway import accounts as accounts_module
from bwtidy.gateway.accounts import (
    MAX_EMAIL,
    MAX_HOST,
    NAME_MAX,
    TEMPORARY_PREFIX,
    AccountError,
    Accounts,
    is_public,
)
from bwtidy.login_state import LoginStateFile
from bwtidy.mcp_token import token_digest
from bwtidy.protocol import ACCOUNT_COOKIE as COOKIE
from bwtidy.protocol import (
    DB_FILE,
    DELETE_MARKER,
    LOGIN_STATE_FILE,
    PENDING_MARKER,
    SUBDIRS,
    TOKEN_FILE,
    WORKER_LOG,
)
from tests.support.gateway_env import (
    ALLOWED,
    EMAIL,
    LOGIN,
    PUBLIC,
    UNLOCK,
    GatewayEnv,
    account,
    assert_unauthorized,
    bearer,
    long_email,
    long_host,
    make_env,
    mcp_token,
    wait_for,
)
from tests.support.quiet import assert_present


@pytest.mark.parametrize(
    "server",
    [
        "http://vault.example.test",
        "https://vault.example.test/path",
        "https://vault.example.test?x=1",
        "https://vault.example.test#f",
        "https://user:pw@vault.example.test",
        "https://private.example.test",
        "https://localhost",
        "https://10.0.0.1",
        "https://mixed.example.test",
        "https://unknown.example.test",
        "https://vault.example.test:99999",
        "https://../vault.example.test",
        "https://[::1]",
        "",
    ],
)
def test_a_server_that_is_not_a_public_https_host_is_refused(env: GatewayEnv, server: str):
    response = env.open(server=server)
    assert response.status_code == 422, response.text
    assert not env.root.exists() or not list(env.root.iterdir())


def test_a_private_address_is_named_in_the_error(env: GatewayEnv):
    response = env.open(server="https://private.example.test")
    assert response.json()["detail"] == (
        "https://private.example.test does not resolve to a public address."
    )


@pytest.mark.parametrize(
    "email",
    [
        "user",
        "user@",
        "@example.test",
        "user@example",
        "user..l@example.test",
        "a/b@example.test",
        ".x@example.test",
        ".new-x@example.test",
    ],
)
def test_an_invalid_email_is_refused(env: GatewayEnv, email: str):
    assert env.open(email=email).status_code == 422


@pytest.mark.parametrize("email", [".x@example.test", ".new-x@example.test"])
def test_a_dotted_email_starts_no_worker_and_makes_no_directory(env: GatewayEnv, email: str):
    """A name with a dot in front would hide among the temporary directories (spec, Gateway)."""
    response = env.open(email=email)
    assert response.status_code == 422, response.text
    assert response.json() == {"detail": "The email is not valid."}
    assert COOKIE not in response.cookies
    assert env.worker_pids() == []
    assert not env.root.exists() or not list(env.root.rglob("*"))


def assert_refused_before_the_disk(env: GatewayEnv, response, detail: str) -> None:
    assert response.status_code == 422, response.text
    assert response.json() == {"detail": detail}
    assert COOKIE not in response.cookies
    assert env.worker_pids() == []
    assert not env.root.exists() or not list(env.root.rglob("*"))


def test_the_longest_email_logs_in_and_gets_its_directory(env: GatewayEnv):
    """Its temporary name is TEMPORARY_PREFIX and the email, a file name of NAME_MAX bytes."""
    email = long_email(MAX_EMAIL)
    assert len(TEMPORARY_PREFIX + email) == NAME_MAX
    env.started(email=email)
    path = env.directory(email=email)
    assert (path / PENDING_MARKER).is_file()
    assert sorted(p.name for p in path.parent.iterdir()) == [email]


@pytest.mark.parametrize("length", [MAX_EMAIL + 1, 254, 255])
def test_an_email_too_long_for_its_temporary_name_is_refused(env: GatewayEnv, length: int):
    response = env.open(email=long_email(length))
    assert_refused_before_the_disk(env, response, "The email is not valid.")


def test_the_longest_host_with_a_port_logs_in_and_gets_its_directory(tmp_path: Path):
    host = long_host(NAME_MAX - len("_8443"))
    server = f"https://{host}:8443"
    for env in make_env(tmp_path, allowed_servers=frozenset({server})):
        env.started(server=server)
        assert (env.directory(f"{host}_8443") / PENDING_MARKER).is_file()


@pytest.mark.parametrize(("length", "port"), [(NAME_MAX - len("_8443") + 1, 8443), (MAX_HOST, 10)])
def test_a_host_too_long_for_its_directory_name_with_the_port_is_refused(
    env: GatewayEnv, length: int, port: int
):
    response = env.open(server=f"https://{long_host(length)}:{port}")
    assert_refused_before_the_disk(env, response, "The server's host name is not valid.")


def test_the_longest_host_without_a_port_is_accepted(tmp_path: Path):
    host = long_host(MAX_HOST)
    server = f"https://{host}"
    for env in make_env(tmp_path, allowed_servers=frozenset({server})):
        env.started(server=f"{server}:443")
        assert (env.directory(host) / PENDING_MARKER).is_file()


def test_an_allowed_server_skips_the_address_check(env: GatewayEnv):
    ## vaultwarden does not resolve in the fake resolver, so only the allow list lets it pass
    token = env.cookie(env.open(server="https://VAULTWARDEN/"))
    assert env.click(token).status_code == 200
    assert env.directory("vaultwarden").is_dir()


@pytest.mark.parametrize(
    ("server", "email", "host_dir", "canonical"),
    [
        (PUBLIC, "User@Example.TEST", "vault.example.test", PUBLIC),
        ("HTTPS://Vault.Example.Test/", EMAIL, "vault.example.test", PUBLIC),
        (f"{PUBLIC}:443", EMAIL, "vault.example.test", PUBLIC),
        (f"{PUBLIC}:8443/", EMAIL, "vault.example.test_8443", f"{PUBLIC}:8443"),
    ],
)
def test_the_directory_name_comes_from_the_host_port_and_lowercased_email(
    env: GatewayEnv, server: str, email: str, host_dir: str, canonical: str
):
    response = env.open(server=server, email=email)
    assert response.status_code == 200, response.text
    assert response.json() == {
        "account": {"server": canonical, "email": EMAIL},
        "running": False,
        "exists": False,
        "pending": False,
        "loggedIn": False,
        "firstRun": True,
    }
    assert env.click(env.cookie(response)).status_code == 200
    assert env.directory(host_dir).is_dir()


def test_a_symlinked_account_directory_is_refused(env: GatewayEnv):
    elsewhere = env.root.parent / "elsewhere"
    elsewhere.mkdir()
    env.directory().parent.mkdir(parents=True)
    env.directory().symlink_to(elsewhere)
    assert env.open().status_code == 422
    assert env.worker_pids() == []


def write_login_state(path: Path, logged_in: bool) -> None:
    """The file the way the account's worker writes it."""
    LoginStateFile(path / "app").record(logged_in)


def test_the_state_reads_whether_the_cli_is_logged_in_from_the_workers_file(env: GatewayEnv):
    token = env.started()
    path = env.directory()
    ## a pending account without the file never logged in
    assert env.account(token)["loggedIn"] is False
    write_login_state(path, True)
    assert env.account(token)["loggedIn"] is True
    (path / PENDING_MARKER).unlink()
    write_login_state(path, False)
    assert env.account(token) == {
        "account": {"server": ALLOWED, "email": EMAIL},
        "running": True,
        "exists": True,
        "pending": False,
        "loggedIn": False,
        "firstRun": True,
    }
    ## the file is the only source: without one, or with other content, logged out
    (path / "app" / LOGIN_STATE_FILE).unlink()
    assert env.account(token)["loggedIn"] is False
    (path / "app" / LOGIN_STATE_FILE).write_bytes(b"logged in\nand more\n")
    assert env.account(token)["loggedIn"] is False


def test_the_state_ignores_a_login_state_file_through_a_symlink_or_of_another_owner(
    env: GatewayEnv, monkeypatch: pytest.MonkeyPatch
):
    token = env.started()
    path = env.directory()
    (path / PENDING_MARKER).unlink()
    elsewhere = env.root.parent / "elsewhere"
    elsewhere.mkdir()
    write_login_state(elsewhere, True)
    ## pointed at a file that says logged in, the gateway does not trust it
    (path / "app" / LOGIN_STATE_FILE).symlink_to(elsewhere / "app" / LOGIN_STATE_FILE)
    assert env.account(token)["loggedIn"] is False
    (path / "app" / LOGIN_STATE_FILE).unlink()
    write_login_state(path, True)
    assert env.account(token)["loggedIn"] is True
    monkeypatch.setattr(accounts_module, "directory_owner", lambda path: os.getuid() + 1)
    assert env.account(token)["loggedIn"] is False


def test_an_account_has_logged_in_once_it_has_its_database_and_no_pending_marker(
    tmp_path: Path,
):
    accounts = Accounts(tmp_path / "a", uid_base=70000, switch_uid=False)
    accounts.prepare()
    ## no account at all
    assert accounts.any_logged_in() is False
    path, _ = accounts.create(account(EMAIL))
    database = path / "app" / DB_FILE
    ## a new account before its worker started, then with the database its worker makes at
    ## its start, the login not done yet (a failed one leaves the same until the directory goes)
    assert accounts.any_logged_in() is False
    database.touch()
    assert accounts.any_logged_in() is False
    ## past its first unlock but without a database
    (path / PENDING_MARKER).unlink()
    database.unlink()
    assert accounts.any_logged_in() is False
    database.touch()
    assert accounts.has_logged_in(path) is True
    assert accounts.any_logged_in() is True
    ## a symlink or a directory in the database's place is no database
    database.unlink()
    elsewhere = tmp_path / "elsewhere.sqlite"
    elsewhere.touch()
    database.symlink_to(elsewhere)
    assert accounts.any_logged_in() is False
    database.unlink()
    database.mkdir()
    assert accounts.any_logged_in() is False


def test_first_run_holds_until_an_account_got_past_its_first_login(env: GatewayEnv):
    """The notice of the login pages (docs/spec.md, The account form), derived, never stored."""
    assert env.account(None)["firstRun"] is True
    token = env.started()
    path = env.directory()
    database = path / "app" / DB_FILE
    database.touch()
    ## the worker's database alone is not a login, the pending marker still says none succeeded
    assert env.account(token)["firstRun"] is True
    (path / PENDING_MARKER).unlink()
    assert env.account(token)["firstRun"] is False
    ## every browser gets it, with or without a routing cookie, with another account
    assert env.account(None)["firstRun"] is False
    opened = env.open(email="peer@example.test")
    assert opened.json()["firstRun"] is False
    assert env.account(env.cookie(opened))["firstRun"] is False
    ## with no account left that logged in, it holds again
    database.unlink()
    assert env.account(None)["firstRun"] is True


def test_create_makes_the_layout_with_the_pending_marker(env: GatewayEnv):
    response = env.open()
    assert response.status_code == 200, response.text
    assert env.click(env.cookie(response)).status_code == 200
    path = env.directory()
    assert (path / PENDING_MARKER).is_file()
    for name in SUBDIRS:
        assert (path / name).is_dir()
        assert (path / name).stat().st_mode & 0o777 == 0o700
    assert path.stat().st_mode & 0o777 == 0o700
    assert path.parent.stat().st_mode & 0o777 == 0o711
    assert env.root.stat().st_mode & 0o777 == 0o711
    assert (path / WORKER_LOG).is_file()
    assert not (path / "run").exists()


def test_the_socket_root_is_no_host_directory(env: GatewayEnv):
    """By default the socket directories lie in the accounts directory, as .run/NAME."""
    env.started()
    (env.root / ".run" / "0123456789abcdef").mkdir(parents=True)
    accounts = env.gateway.accounts
    assert accounts.host_directories() == [env.root / "vaultwarden"]
    assert accounts.account_directories() == [env.directory()]


def test_uid_allocation_takes_the_highest_owner_plus_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    root = tmp_path / "a"
    owners: dict[Path, int] = {}
    monkeypatch.setattr(accounts_module, "directory_owner", lambda path: owners.get(path, 0))
    accounts = Accounts(root, uid_base=70000, switch_uid=False)
    accounts.prepare()
    assert accounts.next_uid() == 70001
    owned = [("h1", "a@x.yz", 70001), ("h1", "b@x.yz", 70005), ("h2", "c@x.yz", 70003)]
    for host_dir, email, uid in owned:
        path = root / host_dir / email
        path.mkdir(parents=True)
        owners[path] = uid
    ## a pending directory counts too, its uid must not be handed out twice
    (root / "h1" / "b@x.yz" / PENDING_MARKER).touch()
    assert accounts.next_uid() == 70006
    ## a symlink is never an account
    (root / "h2" / "link@x.yz").symlink_to(root / "h1" / "b@x.yz")
    owners[root / "h2" / "link@x.yz"] = 80000
    assert accounts.next_uid() == 70006


def test_a_delete_marker_through_a_symlink_deletes_nothing(env: GatewayEnv):
    env.started()
    path = env.directory()
    (path / PENDING_MARKER).unlink()
    elsewhere = env.root.parent / "elsewhere-marker"
    elsewhere.write_bytes(b"")
    ## a worker could point the marker at a file it does not own: the gateway does not follow it
    (path / "app" / DELETE_MARKER).symlink_to(elsewhere)
    [pid] = env.worker_pids()
    os.kill(pid, signal.SIGKILL)
    wait_for(lambda: env.worker_pids() == [], "the worker to exit")
    assert path.is_dir()


def test_a_delete_marker_of_another_owner_deletes_nothing(
    env: GatewayEnv, monkeypatch: pytest.MonkeyPatch
):
    env.started()
    path = env.directory()
    (path / PENDING_MARKER).unlink()
    (path / "app" / DELETE_MARKER).write_bytes(b"")
    ## the marker is a real file, but not owned by the account's uid
    monkeypatch.setattr(accounts_module, "directory_owner", lambda path: os.getuid() + 1)
    [pid] = env.worker_pids()
    os.kill(pid, signal.SIGKILL)
    wait_for(lambda: env.worker_pids() == [], "the worker to exit")
    assert path.is_dir()


def test_pending_directories_are_swept_at_start(tmp_path: Path):
    root = tmp_path / "a"
    pending = root / "vaultwarden" / "gone@example.test"
    kept = root / "vaultwarden" / "kept@example.test"
    for path in (pending, kept):
        (path / "app").mkdir(parents=True)
    (pending / PENDING_MARKER).touch()
    linked = root / "vaultwarden" / "link@example.test"
    target = tmp_path / "target"
    target.mkdir()
    (target / PENDING_MARKER).touch()
    linked.symlink_to(target)
    for _ in make_env(tmp_path):
        assert not pending.exists()
        assert kept.is_dir()
        assert target.is_dir() and (target / PENDING_MARKER).exists()


def test_a_leftover_temporary_directory_is_swept_at_start_and_never_listed(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
):
    root = tmp_path / "a"
    leftover = root / "vaultwarden" / (TEMPORARY_PREFIX + "x@example.test")
    kept = root / "vaultwarden" / "kept@example.test"
    for path in (leftover, kept):
        (path / "app").mkdir(parents=True)
    ## without the pending marker: the crash came before it was made
    accounts = Accounts(root, uid_base=70000, switch_uid=False)
    assert accounts.account_directories() == [kept]
    assert accounts.pending_count() == 0
    with caplog.at_level(logging.INFO, logger="bwtidy"):
        for _ in make_env(tmp_path):
            assert not leftover.exists()
            assert kept.is_dir()
    assert_present(
        "Deleted the unfinished account directory vaultwarden/.new-x@example.test",
        caplog.text,
        "the log",
    )


def test_create_ends_with_the_final_name_and_no_temporary_one(env: GatewayEnv):
    env.started()
    path = env.directory()
    assert path.is_dir() and (path / PENDING_MARKER).is_file()
    assert sorted(p.name for p in path.parent.iterdir()) == [EMAIL]
    ## a leftover from an interrupted create of the same account is replaced
    env.request("DELETE", "/gateway/account", {COOKIE: env.started(email="peer@example.test")})
    leftover = path.parent / (TEMPORARY_PREFIX + "peer@example.test")
    (leftover / "app").mkdir(parents=True)
    env.started(email="peer@example.test")
    assert not leftover.exists()
    assert (path.parent / "peer@example.test" / PENDING_MARKER).is_file()


def owned_accounts(
    env: GatewayEnv, monkeypatch: pytest.MonkeyPatch, owners: dict[str, int]
) -> None:
    """Account directories past their first unlock, each owned by the uid given its email.

    The gateway acts as if it switched uids, so the owner check runs (the unit tests cannot
    chown, and without switching uids every directory is the gateway's own).
    """
    by_path = {env.directory(email=email): uid for email, uid in owners.items()}
    for path in by_path:
        for name in SUBDIRS:
            (path / name).mkdir(parents=True)
    monkeypatch.setattr(accounts_module, "directory_owner", lambda path: by_path.get(path, 0))
    monkeypatch.setattr(env.gateway.accounts, "_switch_uid", True)


@pytest.mark.parametrize("uid", [0, 70000], ids=["root", "uid_base"])
def test_an_account_directory_owned_by_root_or_below_the_base_is_refused(
    env: GatewayEnv, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture, uid: int
):
    owned_accounts(env, monkeypatch, {EMAIL: uid})
    token = env.cookie(env.open(create=False))
    for click in (UNLOCK, LOGIN):
        with caplog.at_level(logging.WARNING, logger="bwtidy"):
            response = env.click(token, click)
        assert response.status_code == 422, response.text
        assert response.json() == {"detail": "The account directory is not usable."}
        assert env.worker_pids() == []
        assert env.gateway.workers.count() == 0
        reason = f"owned by uid {uid}, at or below the uid base 70000"
        assert_present(
            f"Refused the account directory vaultwarden/{EMAIL}: {reason}", caplog.text, "the log"
        )
    ## the directory stays for the operator, with its login state unread as logged out
    assert env.directory().is_dir()
    assert env.account(token)["exists"] is True


def test_two_account_directories_with_one_owner_are_both_refused(
    env: GatewayEnv, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
):
    owned_accounts(
        env,
        monkeypatch,
        {"a@example.test": 70001, "b@example.test": 70001, "c@example.test": 70002},
    )
    with caplog.at_level(logging.WARNING, logger="bwtidy"):
        for email in ("a@example.test", "b@example.test"):
            token = env.cookie(env.open(email=email, create=False))
            response = env.click(token, UNLOCK)
            assert response.status_code == 422, response.text
            assert env.worker_pids() == []
    assert (
        "vaultwarden/a@example.test: owned by uid 70001 like vaultwarden/b@example.test"
        in caplog.text
    )
    assert (
        "vaultwarden/b@example.test: owned by uid 70001 like vaultwarden/a@example.test"
        in caplog.text
    )
    ## the account with its own uid is fine
    token = env.cookie(env.open(email="c@example.test", create=False))
    assert env.click(token, UNLOCK).status_code == 200
    assert len(env.worker_pids()) == 1


def test_the_unusable_directories_are_logged_at_the_gateways_start(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
):
    root = tmp_path / "a"
    owners = {
        root / "h" / "root@x.yz": 0,
        root / "h" / "a@x.yz": 70001,
        root / "h" / "b@x.yz": 70001,
        root / "h" / "fine@x.yz": 70002,
    }
    for path in owners:
        path.mkdir(parents=True)
    monkeypatch.setattr(accounts_module, "directory_owner", lambda path: owners.get(path, 0))
    accounts = Accounts(root, uid_base=70000, switch_uid=True)
    with caplog.at_level(logging.WARNING, logger="bwtidy"):
        refused = accounts.unusable_directories()
    assert refused == [root / "h" / "a@x.yz", root / "h" / "b@x.yz", root / "h" / "root@x.yz"]
    messages = [record.getMessage() for record in caplog.records]
    assert messages == [
        "The account directory h/a@x.yz is not usable: owned by uid 70001 like h/b@x.yz",
        "The account directory h/b@x.yz is not usable: owned by uid 70001 like h/a@x.yz",
        "The account directory h/root@x.yz is not usable: "
        "owned by uid 0, at or below the uid base 70000",
    ]
    with pytest.raises(AccountError):
        accounts.uid_of(root / "h" / "root@x.yz")
    assert accounts.uid_of(root / "h" / "fine@x.yz") == 70002
    ## without switching uids every directory is the gateway's own
    assert Accounts(root, uid_base=70000, switch_uid=False).unusable_directories() == []


def test_the_sweep_never_follows_a_symlinked_host_directory(tmp_path: Path):
    accounts = Accounts(tmp_path / "a", uid_base=70000, switch_uid=False)
    accounts.prepare()
    target = tmp_path / "target"
    (target / "x@y.zz").mkdir(parents=True)
    (target / "x@y.zz" / PENDING_MARKER).touch()
    (tmp_path / "a" / "host").symlink_to(target)
    assert accounts.sweep_pending() == []
    assert (target / "x@y.zz" / PENDING_MARKER).exists()
    with pytest.raises(AccountError):
        accounts.delete(tmp_path / "a" / "host" / "x@y.zz")


def test_mcp_refuses_a_token_file_of_one_line_or_an_expiry_that_cannot_be_read(env: GatewayEnv):
    env.started()
    token = "an-old-token"
    path = env.directory() / "app" / TOKEN_FILE
    path.write_bytes(token_digest(token) + b"\n")
    assert_unauthorized(env.request("POST", "/mcp", {}, headers=bearer(token), json={}))
    ## an expiry that cannot be read is no token
    path.write_bytes(token_digest(token) + b"\nsoon\n")
    assert_unauthorized(env.request("POST", "/mcp", {}, headers=bearer(token), json={}))


def test_mcp_ignores_a_token_file_through_a_symlink_or_of_another_owner(
    env: GatewayEnv, monkeypatch: pytest.MonkeyPatch
):
    env.started(email="user@example.test")
    env.started(email="peer@example.test")
    peer = mcp_token(env, "peer@example.test")
    user_file = env.directory(email="user@example.test") / "app" / TOKEN_FILE
    peer_file = env.directory(email="peer@example.test") / "app" / TOKEN_FILE
    ## the user's worker cannot claim peer's token by pointing at its file
    user_file.symlink_to(peer_file)
    assert env.request("POST", "/mcp", {}, headers=bearer(peer), json={}).json()["email"] == (
        "peer@example.test"
    )
    user_file.unlink()
    ## a copy of peer's hash in the user's directory makes the token ambiguous, it routes nowhere
    shutil.copy(peer_file, user_file)
    assert_unauthorized(env.request("POST", "/mcp", {}, headers=bearer(peer), json={}))
    user_file.unlink()
    ## a file not owned by the account's uid is not the account's
    monkeypatch.setattr(accounts_module, "directory_owner", lambda path: os.getuid() + 1)
    assert_unauthorized(env.request("POST", "/mcp", {}, headers=bearer(peer), json={}))


@pytest.mark.parametrize(
    "host",
    [
        "nat64.example.test",
        "nat64-local.example.test",
        "compat.example.test",
        "compat-loopback.example.test",
        "translated.example.test",
        "mapped.example.test",
        "sixtofour.example.test",
        "teredo.example.test",
        "unspecified.example.test",
        "multicast.example.test",
        "multicast6.example.test",
    ],
)
def test_an_address_that_leads_to_ipv4_or_multicast_is_refused(env: GatewayEnv, host: str):
    response = env.open(server=f"https://{host}")
    assert response.status_code == 422
    assert response.json()["detail"] == f"https://{host} does not resolve to a public address."


def test_a_public_ipv4_mapped_address_is_accepted(env: GatewayEnv):
    assert env.open(server="https://mapped-public.example.test").status_code == 200


@pytest.mark.parametrize(
    ("address", "expected"),
    [
        ("::10.0.0.1", False),
        ("::127.0.0.1", False),
        ("::192.168.1.1", False),
        ("::192.88.99.10", False),
        ("::ffff:0:a00:1", False),
        ("::ffff:10.0.0.1", False),
        ("64:ff9b::a00:1", False),
        ("64:ff9b:1::a00:1", False),
        ("2002:a00:1::", False),
        ("2002:c058:630a::", False),
        ("2001::1", False),
        ("0.0.0.0", False),
        ("::", False),
        ("::1", False),
        ("224.0.0.1", False),
        ("239.255.255.250", False),
        ("ff02::1", False),
        ("ff0e::1", False),
        ("100.64.0.1", False),
        ("fe80::1", False),
        ("::ffff:192.88.99.10", True),
        ("192.88.99.10", True),
        ("2001:20::10", True),
        ("192.88.99.11", True),
        ("2001:20::11", True),
    ],
)
def test_is_public(address: str, expected: bool):
    assert is_public(address) is expected

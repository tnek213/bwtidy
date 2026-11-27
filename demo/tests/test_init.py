import os
import re
import socket
import ssl
import stat
import threading
from pathlib import Path

import pytest
from cryptography import x509

from bwtidy_demo import init

ROOT = Path(__file__).resolve().parents[2]
CONTROLLER = 10100
## owner, group and mode of each file: who reads it (dev/demo.md, Secrets)
FILES = {
    ## Vaultwarden as root without DAC_OVERRIDE (the owner), the controller by its group
    init.ADMIN_TOKEN: (0, CONTROLLER, 0o640),
    ## the controller by its group, the demo gateway as root
    init.CONTROLLER_TOKEN: (0, CONTROLLER, 0o640),
    init.SERVER_KEY: (0, 0, 0o600),
    init.CA_CERT: (0, 0, 0o644),
    init.SERVER_CERT: (0, 0, 0o644),
}


class Owners(dict[str, tuple[int, int]]):
    """What os.chown was asked, by path: the tests do not run as root."""

    def __call__(self, path, uid: int, gid: int, *, follow_symlinks: bool = True) -> None:
        assert follow_symlinks is False or Path(path).is_dir(), path
        self[str(path)] = (uid, gid)

    def of(self, path: Path) -> tuple[int, int]:
        return self[str(path)]


@pytest.fixture(autouse=True)
def owners(monkeypatch: pytest.MonkeyPatch) -> Owners:
    recorded = Owners()
    monkeypatch.setattr(init.os, "chown", recorded)
    return recorded


def mode(path: Path) -> int:
    return stat.S_IMODE(path.lstat().st_mode)


def contents(directory: Path) -> dict[str, bytes]:
    return {name: (directory / name).read_bytes() for name in FILES}


def assert_secured(directory: Path, owners: Owners) -> None:
    for name, (uid, gid, expected) in FILES.items():
        assert owners.of(directory / name) == (uid, gid), name
        assert mode(directory / name) == expected, name
    assert owners.of(directory) == (0, 0)
    assert mode(directory) == 0o755


def test_the_files_exist_with_their_owners_and_modes(tmp_path: Path, owners: Owners):
    directory = tmp_path / "secrets"
    init.init(directory)
    assert {path.name for path in directory.iterdir()} == set(FILES)
    assert_secured(directory, owners)


def test_only_the_controller_and_root_read_the_tokens():
    for name in (init.ADMIN_TOKEN, init.CONTROLLER_TOKEN):
        uid, gid, bits = FILES[name]
        assert (uid, gid) == (0, init.CONTROLLER_GID), name
        assert bits & 0o007 == 0, name
        assert bits & 0o020 == 0, name
    assert FILES[init.SERVER_KEY][2] & 0o077 == 0


def test_a_rerun_over_files_of_root_fixes_their_owners_and_modes(tmp_path: Path, owners: Owners):
    init.init(tmp_path)
    first = contents(tmp_path)
    ## as an older version left them: root's alone, the tokens 0600, the directory 0700
    for name in FILES:
        owners[str(tmp_path / name)] = (0, 0)
        (tmp_path / name).chmod(0o600)
    owners[str(tmp_path)] = (0, 0)
    tmp_path.chmod(0o700)
    assert init.init(tmp_path) is False
    assert contents(tmp_path) == first
    assert_secured(tmp_path, owners)


def test_a_new_file_is_root_s_alone_until_it_is_secured(tmp_path: Path, monkeypatch):
    seen: dict[str, int] = {}
    replace = os.replace

    def watch(source, target) -> None:
        seen[Path(target).name] = mode(Path(source))
        replace(source, target)

    monkeypatch.setattr(init.os, "replace", watch)
    init.init(tmp_path)
    assert seen == dict.fromkeys(FILES, 0o600)


def test_the_controller_ids_are_the_images_and_the_composes():
    dockerfile = (ROOT / "demo" / "Dockerfile").read_text()
    compose = (ROOT / "compose.demo.yaml").read_text()
    assert (init.CONTROLLER_UID, init.CONTROLLER_GID) == (CONTROLLER, CONTROLLER)
    assert f"groupadd --system --gid {CONTROLLER} controller" in dockerfile
    assert f"useradd --system --uid {CONTROLLER} --gid {CONTROLLER}" in dockerfile
    assert re.search(rf'^    user: "{CONTROLLER}:{CONTROLLER}"$', compose, re.MULTILINE)


## --- the ledger


def test_a_ledger_of_root_goes_to_the_controller(tmp_path: Path, owners: Owners):
    ledger = tmp_path / "ledger"
    ledger.mkdir(mode=0o755)
    ids = ledger / "ids"
    ids.write_text("one-two-three-four\n")
    ids.chmod(0o644)
    nested = ledger / "old"
    nested.mkdir()
    (nested / "file").write_text("")
    assert init.fix_ledger(ledger) == 4
    for path in (ledger, nested):
        assert owners.of(path) == (CONTROLLER, CONTROLLER), path
        assert mode(path) == 0o700, path
    for path in (ids, nested / "file"):
        assert owners.of(path) == (CONTROLLER, CONTROLLER), path
        assert mode(path) == 0o600, path
    assert ids.read_text() == "one-two-three-four\n"


def test_the_ledger_changes_hands_bottom_up(tmp_path: Path, owners: Owners):
    (tmp_path / "ids").write_text("")
    init.fix_ledger(tmp_path)
    assert list(owners) == [str(tmp_path / "ids"), str(tmp_path)]


def test_a_symlink_in_the_ledger_is_not_followed(tmp_path: Path, owners: Owners):
    secret = tmp_path / "secret"
    secret.write_text("")
    secret.chmod(0o640)
    ledger = tmp_path / "ledger"
    ledger.mkdir()
    (ledger / "ids").symlink_to(secret)
    init.fix_ledger(ledger)
    assert str(ledger / "ids") not in owners
    assert str(secret) not in owners
    assert mode(secret) == 0o640
    assert owners.of(ledger) == (CONTROLLER, CONTROLLER)


def test_a_ledger_the_controller_owns_is_left_as_it_is(tmp_path: Path, monkeypatch, owners):
    (tmp_path / "ids").write_text("")
    (tmp_path / "ids").chmod(0o644)
    real = os.lstat

    def as_controller(path, *args, **kwargs):
        info = real(path, *args, **kwargs)
        fields = list(info)
        fields[stat.ST_UID] = fields[stat.ST_GID] = CONTROLLER
        return os.stat_result(fields)

    monkeypatch.setattr(Path, "lstat", lambda self: as_controller(self))
    assert init.fix_ledger(tmp_path) == 0
    assert owners == {}
    assert mode(tmp_path / "ids") == 0o644


def test_no_ledger_directory_is_nothing_to_fix(tmp_path: Path, owners: Owners):
    assert init.fix_ledger(tmp_path / "missing") == 0
    assert owners == {}


def test_the_tokens_are_long_and_random(tmp_path: Path):
    init.init(tmp_path)
    admin = (tmp_path / init.ADMIN_TOKEN).read_text()
    controller = (tmp_path / init.CONTROLLER_TOKEN).read_text()
    assert len(admin) >= 40
    assert len(controller) >= 40
    assert admin != controller


def test_a_second_run_keeps_a_full_set(tmp_path: Path):
    assert init.init(tmp_path) is True
    first = contents(tmp_path)
    assert init.init(tmp_path) is False
    assert contents(tmp_path) == first
    assert {path.name for path in tmp_path.iterdir()} == set(FILES)


@pytest.mark.parametrize("name", FILES)
def test_a_set_that_lacks_a_file_is_written_anew(tmp_path: Path, name: str, owners: Owners):
    init.init(tmp_path)
    first = contents(tmp_path)
    (tmp_path / name).unlink()
    owners.clear()
    assert init.init(tmp_path) is True
    second = contents(tmp_path)
    for other in FILES:
        assert first[other] != second[other], other
    assert_secured(tmp_path, owners)
    assert {path.name for path in tmp_path.iterdir()} == set(FILES)


def test_an_empty_file_does_not_count(tmp_path: Path):
    init.init(tmp_path)
    first = contents(tmp_path)
    (tmp_path / init.CONTROLLER_TOKEN).write_bytes(b"")
    assert init.init(tmp_path) is True
    token = (tmp_path / init.CONTROLLER_TOKEN).read_bytes()
    assert token not in (b"", first[init.CONTROLLER_TOKEN])


def test_the_server_certificate_is_written_last():
    assert init.FILES[-1] == init.SERVER_CERT
    assert set(init.FILES) == set(FILES)


@pytest.fixture
def volumes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path]:
    """The secrets directory and the ledger directory run reads from the environment."""
    secrets, ledger = tmp_path / "secrets", tmp_path / "ledger"
    ledger.mkdir()
    monkeypatch.setenv("DEMO_SECRETS", str(secrets))
    monkeypatch.setenv("DEMO_LEDGER", str(ledger / "ids"))
    return secrets, ledger


def test_run_says_whether_it_wrote_or_kept(volumes, caplog):
    with caplog.at_level("INFO"):
        init.run()
        init.run()
    messages = [record.getMessage() for record in caplog.records]
    words = [message.split(" ")[0] for message in messages if "secrets" in message]
    assert words == ["wrote", "kept"]
    assert messages[1].startswith("gave the ledger")


def test_run_writes_into_demo_secrets_and_fixes_demo_ledger(volumes, owners: Owners):
    secrets, ledger = volumes
    init.run()
    assert set(contents(secrets)) == set(FILES)
    assert_secured(secrets, owners)
    assert owners.of(ledger) == (CONTROLLER, CONTROLLER)


def test_run_fails_on_a_directory_it_cannot_write(tmp_path: Path, monkeypatch):
    blocker = tmp_path / "file"
    blocker.write_text("")
    monkeypatch.setenv("DEMO_SECRETS", str(blocker / "secrets"))
    with pytest.raises(SystemExit) as exit:
        init.run()
    assert exit.value.code == 1


def test_run_fails_when_it_cannot_give_the_ledger(volumes, monkeypatch):
    def refuse(path, uid, gid, *, follow_symlinks=True) -> None:
        if uid == CONTROLLER:
            raise PermissionError(1, "Operation not permitted")

    monkeypatch.setattr(init.os, "chown", refuse)
    with pytest.raises(SystemExit) as exit:
        init.run()
    assert exit.value.code == 1


def test_the_server_certificate_is_for_vaultwarden_signed_by_the_ca(tmp_path: Path):
    init.init(tmp_path)
    ca = x509.load_pem_x509_certificate((tmp_path / init.CA_CERT).read_bytes())
    chain = x509.load_pem_x509_certificates((tmp_path / init.SERVER_CERT).read_bytes())
    server = chain[0]
    assert chain[1] == ca
    server.verify_directly_issued_by(ca)
    names = server.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
    assert names.get_values_for_type(x509.DNSName) == ["vaultwarden"]


def handshake(directory: Path, hostname: str) -> None:
    """A TLS handshake with a server on the certificate, as a client that trusts the CA only."""
    server_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    server_context.load_cert_chain(directory / init.SERVER_CERT, directory / init.SERVER_KEY)
    client_context = ssl.create_default_context(cafile=str(directory / init.CA_CERT))
    near, far = socket.socketpair()
    errors: list[Exception] = []

    def serve() -> None:
        try:
            with server_context.wrap_socket(far, server_side=True) as tls:
                tls.recv(1)
        except (ssl.SSLError, OSError) as error:
            errors.append(error)

    thread = threading.Thread(target=serve)
    thread.start()
    try:
        with client_context.wrap_socket(near, server_hostname=hostname) as tls:
            tls.sendall(b"x")
    finally:
        near.close()
        thread.join(5)


def test_a_strict_client_trusting_the_ca_accepts_the_server(tmp_path: Path):
    init.init(tmp_path)
    assert ssl.create_default_context().verify_flags & ssl.VERIFY_X509_STRICT
    handshake(tmp_path, "vaultwarden")


def test_the_server_certificate_is_for_no_other_name(tmp_path: Path):
    init.init(tmp_path)
    with pytest.raises(ssl.SSLCertVerificationError):
        handshake(tmp_path, "bwtidy.example")


def test_the_ca_key_is_not_kept(tmp_path: Path):
    init.init(tmp_path)
    keys = [path for path in tmp_path.iterdir() if b"PRIVATE KEY" in path.read_bytes()]
    assert [path.name for path in keys] == [init.SERVER_KEY]

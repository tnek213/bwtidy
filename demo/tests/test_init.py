import socket
import ssl
import stat
import threading
from pathlib import Path

import pytest
from cryptography import x509

from bwtidy_demo import init

FILES = {
    init.ADMIN_TOKEN: 0o600,
    init.CONTROLLER_TOKEN: 0o600,
    init.SERVER_KEY: 0o600,
    init.CA_CERT: 0o644,
    init.SERVER_CERT: 0o644,
}


def mode(path: Path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


def contents(directory: Path) -> dict[str, bytes]:
    return {name: (directory / name).read_bytes() for name in FILES}


def test_the_files_exist_with_their_modes(tmp_path: Path):
    directory = tmp_path / "secrets"
    init.init(directory)
    assert {path.name for path in directory.iterdir()} == set(FILES)
    for name, expected in FILES.items():
        assert mode(directory / name) == expected, name
    assert mode(directory) == 0o755


def test_the_tokens_are_long_and_random(tmp_path: Path):
    init.init(tmp_path)
    admin = (tmp_path / init.ADMIN_TOKEN).read_text()
    controller = (tmp_path / init.CONTROLLER_TOKEN).read_text()
    assert len(admin) >= 40
    assert len(controller) >= 40
    assert admin != controller


def test_a_second_run_replaces_every_file(tmp_path: Path):
    init.init(tmp_path)
    first = contents(tmp_path)
    ## a file left too open by hand gets its mode back
    (tmp_path / init.ADMIN_TOKEN).chmod(0o666)
    init.init(tmp_path)
    second = contents(tmp_path)
    for name in FILES:
        assert first[name] != second[name], name
        assert mode(tmp_path / name) == FILES[name], name
    assert {path.name for path in tmp_path.iterdir()} == set(FILES)


def test_run_writes_into_demo_secrets(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("DEMO_SECRETS", str(tmp_path / "volume"))
    init.run()
    assert set(contents(tmp_path / "volume")) == set(FILES)


def test_run_fails_on_a_directory_it_cannot_write(tmp_path: Path, monkeypatch):
    blocker = tmp_path / "file"
    blocker.write_text("")
    monkeypatch.setenv("DEMO_SECRETS", str(blocker / "secrets"))
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

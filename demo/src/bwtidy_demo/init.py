"""The stack's secrets, written into the secrets volume once (bwtidy-demo-init).

compose starts the init container again at every `up`, also one that leaves Vaultwarden
running. Vaultwarden reads its certificate and its admin token only when it starts, so new
secrets under a running Vaultwarden would cut the controller and the workers off from it. A
directory that holds every file is therefore left as it is. Removing the volume gives new ones.

Every container of the demo image runs as root, as does Vaultwarden, so the tokens and the
server's key are 0600. The certificates are 0644 and the directory 0755: a worker runs bw as
its account's uid and must read the CA through NODE_EXTRA_CA_CERTS.

The CA's key is never written: it signs the server certificate once and is gone, so nothing
can sign another certificate the stack would trust.
"""

import datetime
import logging
import os
import secrets
import sys
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

log = logging.getLogger(__name__)

SECRETS_DIR = Path("/secrets")
ADMIN_TOKEN = "vaultwarden-admin-token"
CONTROLLER_TOKEN = "controller-token"
CA_CERT = "ca.crt"
SERVER_CERT = "vaultwarden.crt"
SERVER_KEY = "vaultwarden.key"
SERVER_NAME = "vaultwarden"
## in the order init writes them: the server certificate last, so a write that was cut off
## never leaves a full set
FILES = (ADMIN_TOKEN, CONTROLLER_TOKEN, SERVER_KEY, CA_CERT, SERVER_CERT)
## the secrets stay until their volume is removed, so the stack works for as long as it is kept
VALIDITY = datetime.timedelta(days=3650)

PRIVATE = 0o600
PUBLIC = 0o644
DIRECTORY = 0o755


def token() -> str:
    return secrets.token_urlsafe(32)


def write(path: Path, content: bytes, mode: int) -> None:
    """Write path with mode in one rename, so no reader sees a partly written file.

    The temporary file has its mode from its creation, so the content is never readable by
    more than mode allows.
    """
    tmp = path.with_name(f".{path.name}.tmp")
    tmp.unlink(missing_ok=True)
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
    try:
        os.fchmod(fd, mode)
        os.write(fd, content)
        os.fsync(fd)
    finally:
        os.close(fd)
    os.replace(tmp, path)


def certificates(
    name: str = SERVER_NAME, now: datetime.datetime | None = None
) -> tuple[bytes, bytes, bytes]:
    """A fresh CA and a server certificate for name it signs: (CA PEM, server PEM, key PEM).

    The extensions are what Python's VERIFY_X509_STRICT (on by default since 3.13) and Node
    ask of a CA and a server certificate.
    """
    now = datetime.datetime.now(datetime.UTC) if now is None else now
    ca_key = ec.generate_private_key(ec.SECP256R1())
    ca_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "bwtidy demo CA")])
    ca_ski = x509.SubjectKeyIdentifier.from_public_key(ca_key.public_key())
    ca = (
        x509.CertificateBuilder()
        .subject_name(ca_name)
        .issuer_name(ca_name)
        .public_key(ca_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(minutes=5))
        .not_valid_after(now + VALIDITY)
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
        .add_extension(
            x509.KeyUsage(
                digital_signature=False,
                content_commitment=False,
                key_encipherment=False,
                data_encipherment=False,
                key_agreement=False,
                key_cert_sign=True,
                crl_sign=True,
                encipher_only=False,
                decipher_only=False,
            ),
            critical=True,
        )
        .add_extension(ca_ski, critical=False)
        .sign(ca_key, hashes.SHA256())
    )
    key = ec.generate_private_key(ec.SECP256R1())
    server = (
        x509.CertificateBuilder()
        .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, name)]))
        .issuer_name(ca_name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(minutes=5))
        .not_valid_after(now + VALIDITY)
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(
            x509.KeyUsage(
                digital_signature=True,
                content_commitment=False,
                key_encipherment=False,
                data_encipherment=False,
                key_agreement=False,
                key_cert_sign=False,
                crl_sign=False,
                encipher_only=False,
                decipher_only=False,
            ),
            critical=True,
        )
        .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
        .add_extension(x509.SubjectAlternativeName([x509.DNSName(name)]), critical=False)
        .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()), critical=False)
        .add_extension(
            x509.AuthorityKeyIdentifier.from_issuer_subject_key_identifier(ca_ski),
            critical=False,
        )
        .sign(ca_key, hashes.SHA256())
    )
    key_pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    return (
        ca.public_bytes(serialization.Encoding.PEM),
        server.public_bytes(serialization.Encoding.PEM),
        key_pem,
    )


def complete(directory: Path) -> bool:
    """Whether directory holds every secret, each with content."""
    try:
        return all((directory / name).stat().st_size > 0 for name in FILES)
    except OSError:
        return False


def init(directory: Path) -> bool:
    """Write every secret of the stack into directory, unless it holds them all: True if written.

    A directory that lacks one (a first start, or a write that was cut off) gets all of them anew.
    """
    directory.mkdir(parents=True, exist_ok=True)
    directory.chmod(DIRECTORY)
    if complete(directory):
        return False
    ca, server, key = certificates()
    write(directory / ADMIN_TOKEN, token().encode(), PRIVATE)
    write(directory / CONTROLLER_TOKEN, token().encode(), PRIVATE)
    write(directory / SERVER_KEY, key, PRIVATE)
    write(directory / CA_CERT, ca, PUBLIC)
    ## the chain, so a client that is given only the CA can build the path
    write(directory / SERVER_CERT, server + ca, PUBLIC)
    return True


def run() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    directory = Path(os.environ.get("DEMO_SECRETS", str(SECRETS_DIR)))
    try:
        written = init(directory)
    except OSError as error:
        log.error("could not write the secrets into %s: %s", directory, error.strerror)
        sys.exit(1)
    if written:
        log.info("wrote the stack's secrets into %s", directory)
    else:
        log.info("kept the stack's secrets in %s", directory)

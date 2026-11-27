"""The stack's secrets, written into the secrets volume once, and their owners (bwtidy-demo-init).

compose starts the init container again at every `up`, also one that leaves Vaultwarden
running. Vaultwarden reads its certificate and its admin token only when it starts, so new
secrets under a running Vaultwarden would cut the controller and the workers off from it. A
directory that holds every file is therefore left as it is. Removing the volume gives new ones.

Every file is root's, its group and mode let exactly its readers in (dev/demo.md, Secrets).
Vaultwarden runs as root without DAC_OVERRIDE and reads the admin token and the key as their
owner. The controller runs as CONTROLLER_UID and reads both tokens through its group. The demo
gateway runs as root. The certificates are 0644 and the directory 0755: a worker runs bw as
its account's uid and must read the CA through NODE_EXTRA_CA_CERTS. The owners and modes are
set at every start, also on a set that is kept, so a volume an older version wrote gets them.

The ledger volume is the controller's. One that an older version made as root is given to the
controller here, before the controller starts.

The CA's key is never written: it signs the server certificate once and is gone, so nothing
can sign another certificate the stack would trust.
"""

import datetime
import logging
import os
import secrets
import stat
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
## the default of the controller's DEMO_LEDGER, init fixes its directory
LEDGER = Path("/ledger/ids")

## the controller's user and group, as demo/Dockerfile makes them and compose.demo.yaml runs it
CONTROLLER_UID = 10100
CONTROLLER_GID = 10100
ROOT = 0

PRIVATE = 0o600
GROUP = 0o640
PUBLIC = 0o644
DIRECTORY = 0o755
LEDGER_FILE = 0o600
LEDGER_DIRECTORY = 0o700

## the group and mode of each file, every one owned by root
ACCESS = {
    ADMIN_TOKEN: (CONTROLLER_GID, GROUP),
    CONTROLLER_TOKEN: (CONTROLLER_GID, GROUP),
    SERVER_KEY: (ROOT, PRIVATE),
    CA_CERT: (ROOT, PUBLIC),
    SERVER_CERT: (ROOT, PUBLIC),
}


def token() -> str:
    return secrets.token_urlsafe(32)


def write(path: Path, content: bytes) -> None:
    """Write path in one rename, so no reader sees a partly written file.

    The file is root's alone (0600) from its creation until secure gives it its group and mode.
    """
    tmp = path.with_name(f".{path.name}.tmp")
    tmp.unlink(missing_ok=True)
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, PRIVATE)
    try:
        os.fchmod(fd, PRIVATE)
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


def secure(directory: Path) -> None:
    """Give directory and every secret in it their owner, group and mode (ACCESS)."""
    os.chown(directory, ROOT, ROOT)
    directory.chmod(DIRECTORY)
    for name in FILES:
        group, mode = ACCESS[name]
        path = directory / name
        os.chown(path, ROOT, group, follow_symlinks=False)
        path.chmod(mode)


def init(directory: Path) -> bool:
    """Write every secret of the stack into directory, unless it holds them all: True if written.

    A directory that lacks one (a first start, or a write that was cut off) gets all of them anew.
    Either way every file gets its owner, group and mode.
    """
    directory.mkdir(parents=True, exist_ok=True)
    written = not complete(directory)
    if written:
        ca, server, key = certificates()
        write(directory / ADMIN_TOKEN, token().encode())
        write(directory / CONTROLLER_TOKEN, token().encode())
        write(directory / SERVER_KEY, key)
        write(directory / CA_CERT, ca)
        ## the chain, so a client that is given only the CA can build the path
        write(directory / SERVER_CERT, server + ca)
    secure(directory)
    return written


def fix_ledger(directory: Path) -> int:
    """Give the controller what root owns in its ledger directory: how many entries changed.

    Bottom up, so a directory is still root's while its entries change hands and the controller
    cannot swap one for a link meanwhile. A symlink is never followed and keeps its owner. A
    directory the controller owns already is left as it is: what it holds is its own, and root
    without DAC_OVERRIDE could not list it anyway.
    """
    try:
        top = directory.lstat()
    except FileNotFoundError:
        return 0
    if not stat.S_ISDIR(top.st_mode) or top.st_uid == CONTROLLER_UID:
        return 0
    changed = 0
    for parent, dirs, files in os.walk(directory, topdown=False):
        for name in [*files, *dirs]:
            changed += give(Path(parent) / name)
    return changed + give(directory)


def give(path: Path) -> int:
    """Give one file or directory to the controller, set its mode first while root owns it."""
    info = path.lstat()
    if stat.S_ISDIR(info.st_mode):
        mode = LEDGER_DIRECTORY
    elif stat.S_ISREG(info.st_mode):
        mode = LEDGER_FILE
    else:
        return 0
    if (info.st_uid, info.st_gid) == (CONTROLLER_UID, CONTROLLER_GID):
        return 0
    ## without FOWNER only the owner sets a mode, so before the chown
    if info.st_uid == os.geteuid():
        path.chmod(mode)
    os.chown(path, CONTROLLER_UID, CONTROLLER_GID, follow_symlinks=False)
    return 1


def run() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    directory = Path(os.environ.get("DEMO_SECRETS", str(SECRETS_DIR)))
    ledger = Path(os.environ.get("DEMO_LEDGER", str(LEDGER))).parent
    try:
        written = init(directory)
    except OSError as error:
        log.error("could not write the secrets into %s: %s", directory, error.strerror)
        sys.exit(1)
    if written:
        log.info("wrote the stack's secrets into %s", directory)
    else:
        log.info("kept the stack's secrets in %s", directory)
    try:
        changed = fix_ledger(ledger)
    except OSError as error:
        log.error("could not give the ledger in %s to the controller: %s", ledger, error.strerror)
        sys.exit(1)
    if changed:
        log.info("gave the ledger in %s to the controller (%d changed)", ledger, changed)

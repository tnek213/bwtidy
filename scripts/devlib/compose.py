"""docker compose of a dev stack and its TLS: the self-signed certificate, Vaultwarden's wait."""

import ssl
import subprocess
import time
import urllib.request
from pathlib import Path

from devlib.paths import ROOT
from devlib.stacks import Stack

COMPOSE_FILE = "compose.dev.yaml"


def compose(stack: Stack, *args: str) -> list[str]:
    """docker compose ARGS of the stack, as a command."""
    path = stack.root / COMPOSE_FILE
    return ["docker", "compose", "-f", str(path), "--env-file", str(stack.env_file), *args]


def run(cmd: list[str], **kwargs) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, cwd=ROOT, check=True, **kwargs)


def make_cert(stack: Stack) -> None:
    if stack.cert.exists() and stack.key.exists():
        return
    stack.dir.mkdir(exist_ok=True)
    openssl = (
        "openssl req -x509 -nodes -days 3650 -subj /CN=bwtidy-dev-vault"
        " -newkey ec -pkeyopt ec_paramgen_curve:prime256v1"
        " -addext subjectAltName=DNS:vaultwarden,DNS:localhost,IP:127.0.0.1"
        " -addext basicConstraints=critical,CA:TRUE"
    )
    run([*openssl.split(), "-keyout", str(stack.key), "-out", str(stack.cert)], capture_output=True)


def vault_url(env: dict[str, str]) -> str:
    return f"https://localhost:{env['BWTIDY_VW_PORT']}"


def wait_alive(url: str, cert: Path, timeout: float = 90) -> None:
    context = ssl.create_default_context(cafile=str(cert))
    deadline = time.monotonic() + timeout
    while True:
        try:
            with urllib.request.urlopen(f"{url}/alive", context=context, timeout=5) as response:
                if response.status == 200:
                    return
        except OSError:
            pass
        if time.monotonic() > deadline:
            raise SystemExit(f"dev-vault: Vaultwarden did not answer on {url}/alive")
        time.sleep(1)


def container(stack: Stack, service: str) -> str:
    """The id of the service's container, also when it is stopped."""
    command = compose(stack, "ps", "-a", "-q", service)
    return run(command, capture_output=True, text=True).stdout.strip()

"""The snapshot of a seeded stack: a local image named after a hash of what shapes the seed.

It holds Vaultwarden's /data and the dev account's CLI state. Only stack 1 saves it, the stacks
from 2 up start from it.
"""

import hashlib
import subprocess
import tempfile
import time
from pathlib import Path

from devlib import compose
from devlib.paths import ROOT
from devlib.stacks import Stack, env_file, read_env

## the snapshot of a seeded stack, a local image tagged with snapshot_hash()
SNAPSHOT_REPO = "bwtidy-dev-vaultwarden"
## when the snapshot was saved, in seconds since the epoch
SNAPSHOT_CREATED = "bwtidy.dev-vault-snapshot.created"
## Vaultwarden's refresh tokens last 30 days, so the CLI login in an older snapshot could not sync
SNAPSHOT_MAX_AGE = 14 * 24 * 3600
## where the snapshot image keeps the dev account's CLI state, beside Vaultwarden's /data
SNAPSHOT_BW_DIR = "/bwtidy-snapshot/bw"
APP_BW_DIR = "/data/bw"
## how long a stack from 2 up waits for the snapshot that stack 1's setup is building
SNAPSHOT_WAIT = 900


def snapshot_inputs(root: Path) -> list[Path]:
    """Every file that shapes the seeded vault, in a fixed order, never a guide."""
    files = [
        root / "scripts" / "dev-vault",
        root / "scripts" / "devlib" / "compose.py",
        root / "scripts" / "devlib" / "paths.py",
        root / "scripts" / "devlib" / "seed.py",
        root / "scripts" / "devlib" / "snapshot.py",
        root / "scripts" / "devlib" / "stacks.py",
        root / "compose.dev.yaml",
        root / "Dockerfile",
        root / "docker" / "unref-share-timers.js",
        root / "docker" / "bitwarden-cli" / "package.json",
        root / "docker" / "bitwarden-cli" / "package-lock.json",
    ]
    fixtures = sorted((root / "dev").glob("fixture-*.json"))
    attachments = sorted(p for p in (root / "dev" / "attachments").glob("*") if p.is_file())
    return files + fixtures + attachments


def snapshot_hash(root: Path) -> str:
    digest = hashlib.sha256()
    for path in snapshot_inputs(root):
        name = path.relative_to(root).as_posix().encode()
        data = path.read_bytes()
        ## lengths first, so moving bytes between a name and a file's content changes the hash
        for part in (name, data):
            digest.update(len(part).to_bytes(8, "big") + part)
    return digest.hexdigest()[:16]


def snapshot_image(root: Path = ROOT) -> str:
    """The snapshot image of the inputs under root, this checkout's by default."""
    return f"{SNAPSHOT_REPO}:{snapshot_hash(root)}"


def snapshot_age(image: str) -> float | None:
    """Seconds since the snapshot image was saved, None when there is no such image."""
    template = f'{{{{index .Config.Labels "{SNAPSHOT_CREATED}"}}}}'
    result = subprocess.run(
        ["docker", "image", "inspect", "--format", template, image], capture_output=True, text=True
    )
    if result.returncode != 0:
        return None
    try:
        return time.time() - float(result.stdout.strip())
    except ValueError:
        return float("inf")


def snapshot_usable(image: str) -> bool:
    age = snapshot_age(image)
    return age is not None and age < SNAPSHOT_MAX_AGE


def snapshot_dockerfile(base: str, digest: str, created: int) -> str:
    return (
        f"FROM {base}\n"
        f"LABEL bwtidy.dev-vault-snapshot={digest} {SNAPSHOT_CREATED}={created}\n"
        "COPY data/ /data/\n"
        f"COPY --chown=1000:1000 bw/ {SNAPSHOT_BW_DIR}/\n"
    )


def save_snapshot(stack: Stack, image: str) -> None:
    """Save the stack setup just seeded as the image: Vaultwarden's /data and the CLI state."""
    print(f"dev-vault: saving the seeded vault as {image}")
    vaultwarden, app = compose.container(stack, "vaultwarden"), compose.container(stack, "app")
    base = compose.run(
        ["docker", "inspect", "--format", "{{.Config.Image}}", vaultwarden],
        capture_output=True,
        text=True,
    ).stdout.strip()
    ## a stopped Vaultwarden writes nothing, so db.sqlite3 and its WAL are copied in one state
    compose.run(compose.compose(stack, "stop", "vaultwarden"))
    try:
        with tempfile.TemporaryDirectory(prefix="dev-vault-snapshot-") as tmp:
            compose.run(
                ["docker", "cp", f"{vaultwarden}:/data", f"{tmp}/data"], capture_output=True
            )
            compose.run(["docker", "cp", f"{app}:{APP_BW_DIR}", f"{tmp}/bw"], capture_output=True)
            dockerfile = snapshot_dockerfile(base, image.split(":")[-1], int(time.time()))
            Path(tmp, "Dockerfile").write_text(dockerfile)
            compose.run(["docker", "build", "-q", "-t", image, tmp], stdout=subprocess.DEVNULL)
    finally:
        compose.run(compose.compose(stack, "start", "vaultwarden"))
        compose.wait_alive(compose.vault_url(stack.env()), stack.cert)
    print(f"dev-vault: saved {image}")


def restore_cli_state(stack: Stack) -> None:
    """Copy the dev account's CLI state from the snapshot image into a fresh bw-data volume."""
    present = compose.compose(stack, "exec", "-T", "app", "test", "-e", f"{APP_BW_DIR}/data.json")
    if subprocess.run(present, cwd=ROOT).returncode == 0:
        return
    source = subprocess.Popen(
        compose.compose(
            stack, "exec", "-T", "vaultwarden", "tar", "-C", SNAPSHOT_BW_DIR, "-cf", "-", "."
        ),
        cwd=ROOT,
        stdout=subprocess.PIPE,
    )
    assert source.stdout is not None
    try:
        compose.run(
            compose.compose(stack, "exec", "-T", "app", "tar", "-C", APP_BW_DIR, "-xf", "-"),
            stdin=source.stdout,
        )
    finally:
        source.stdout.close()
        returncode = source.wait()
    if returncode != 0:
        raise SystemExit("dev-vault: could not read the CLI state from the snapshot image")
    print("dev-vault: copied the CLI state from the snapshot")


def stack_one_builds_snapshot(root: Path) -> bool:
    """True while stack 1 runs from a bare Vaultwarden, so its setup saves a snapshot."""
    path = env_file(root, 1)
    return path.exists() and not read_env(path).get("BWTIDY_VW_IMAGE")


def wait_for_snapshot(stack: Stack, snapshot: str) -> None:
    """A stack from 2 up only starts from the snapshot: wait while stack 1 builds it."""
    deadline = time.monotonic() + SNAPSHOT_WAIT
    if not snapshot_usable(snapshot) and stack_one_builds_snapshot(stack.root):
        print(f"dev-vault: waiting for stack 1 to save the snapshot {snapshot}", flush=True)
    while not snapshot_usable(snapshot):
        if not stack_one_builds_snapshot(stack.root) or time.monotonic() > deadline:
            raise SystemExit(
                f"dev-vault: stack {stack.number} starts only from the snapshot {snapshot}, which"
                " stack 1 builds: run scripts/dev-vault reset without BWTIDY_STACK first"
            )
        time.sleep(5)


def keep_snapshot(stack: Stack, seeded: bool) -> None:
    """Save the stack as the snapshot image of its inputs, when setup seeded it just now.

    Only a stack this setup seeded is saved, one that skipped the seed may hold what tests wrote.
    Only stack 1 saves one, so two stacks never build the same image at once.
    """
    if not seeded or stack.number > 1:
        return
    image = snapshot_image()
    if snapshot_usable(image):
        print(f"dev-vault: the snapshot {image} exists")
        return
    save_snapshot(stack, image)

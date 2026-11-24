"""What scripts/e2e and scripts/integration share: options, test paths and the sleep lock."""

import os
import shutil
import subprocess
import sys

## where a test path outside a test script's suite belongs, the first match wins
SUITES = [
    ("tests/e2e", "scripts/e2e"),
    ("tests/integration", "scripts/integration"),
    ("tests", "cd backend && uv run --locked pytest"),
]
## marks the re-exec under systemd-inhibit, so it happens once
INHIBITED = "BWTIDY_INHIBITED"


def split_options(args: list[str], flags: list[str]) -> tuple[dict[str, str | None], list[str]]:
    """The script's own options (its flags, --stacks N, anywhere) apart from pytest's arguments."""
    options: dict[str, str | None] = {}
    rest = []
    index = 0
    while index < len(args):
        arg = args[index]
        if arg in flags:
            options[arg] = None
        elif arg == "--stacks" and index + 1 < len(args):
            index += 1
            options[arg] = args[index]
        elif arg.startswith("--stacks="):
            options["--stacks"] = arg.partition("=")[2]
        else:
            rest.append(arg)
        index += 1
    return options, rest


def wants_help(args: list[str]) -> bool:
    """-h or --help anywhere before a `--`: the script prints its usage and does nothing else."""
    before = args[: args.index("--")] if "--" in args else args
    return "-h" in before or "--help" in before


def under(path: str, directory: str) -> bool:
    return path == directory or path.startswith((f"{directory}/", f"{directory}::"))


def pytest_args(args: list[str], name: str, suite: str, flags: list[str]) -> list[str]:
    """Turn the script's arguments into pytest's: with a test path, only the tests named run.

    name is the script's. A test path outside its suite exits naming the script it belongs to.
    """
    _, args = split_options(args, flags)
    result = []
    named = False
    for arg in args:
        ## only the start decides, so the NAME after -k is never a path
        if arg.startswith("backend/tests/"):
            arg = arg.removeprefix("backend/")
        if arg.startswith("tests/"):
            if not under(arg, suite):
                runner = next(runner for directory, runner in SUITES if under(arg, directory))
                raise SystemExit(f"{name}: {arg} is not an {name} test, run it with {runner}")
            named = True
        result.append(arg)
    return result if named else [suite, *result]


def stacks_option(value: str | None, name: str) -> int | None:
    """--stacks N as a number, None without it. SystemExit (from name) for anything but 1 up."""
    if value is None:
        return None
    if not value.isdigit() or int(value) < 1:
        raise SystemExit(f"{name}: --stacks takes a number from 1 up, not {value!r}")
    return int(value)


def reexec_inhibited(name: str, script: str) -> None:
    """Re-run the script under a sleep and idle lock, so a long run is not suspended.

    Falls back to a plain run with one line on stderr when systemd-inhibit is missing or
    refuses the lock. A closed lid still suspends: logind ignores inhibitors for it.
    """
    if os.environ.get(INHIBITED):
        return
    inhibit = shutil.which("systemd-inhibit")
    command = [inhibit, "--what=sleep:idle", "--why=bwtidy tests"] if inhibit else []
    if not command:
        print(f"{name}: no systemd-inhibit, running without a sleep lock", file=sys.stderr)
        return
    ## a refused lock would make systemd-inhibit exit before the tests start
    probe = subprocess.run([*command, "true"], stdin=subprocess.DEVNULL, capture_output=True)
    if probe.returncode != 0:
        print(f"{name}: systemd-inhibit refused the lock, running without it", file=sys.stderr)
        return
    env = dict(os.environ, **{INHIBITED: "1"})
    os.execve(command[0], [*command, sys.executable, script, *sys.argv[1:]], env)

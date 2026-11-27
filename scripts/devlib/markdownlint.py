"""markdownlint: every Markdown file of the repo follows the rules of .markdownlint-cli2.jsonc.

markdownlint-cli2 comes from its own npm folder, tools/markdownlint, installed with npm ci from its
lock file the first time a check needs it and again after the lock file changed (the stamp of
locks.npm_install_needed). It reads the rules, the files to lint (globs) and the files to leave out
(ignores) from .markdownlint-cli2.jsonc in the directory it runs in, the file the VS Code extension
markdownlint reads too. The repo's own rules (customRules) are modules in tools/markdownlint/rules.
"""

import subprocess
from pathlib import Path

from devlib import locks
from devlib.paths import ROOT

TOOL = Path("tools") / "markdownlint"


def install(root: Path = ROOT) -> None:
    """npm ci in tools/markdownlint, unless node_modules there holds its lock file already."""
    if not locks.npm_install_needed(TOOL, root):
        return
    print(f"markdownlint: installing {TOOL.as_posix()} with npm ci", flush=True)
    subprocess.run(["npm", "ci", "--no-fund", "--no-audit"], cwd=root / TOOL, check=True)
    locks.write_npm_stamp(TOOL, root)


def lint(where: Path = ROOT, fix: bool = False, root: Path = ROOT) -> str | None:
    """The findings in WHERE (fixed first when FIX), one per line, else None.

    Each finding is markdownlint-cli2's FILE:LINE[:COLUMN] error RULE/NAME DESCRIPTION.
    """
    install(root)
    binary = root / TOOL / "node_modules" / ".bin" / "markdownlint-cli2"
    command = [str(binary), *(["--fix"] if fix else [])]
    result = subprocess.run(command, cwd=where, capture_output=True, text=True)
    if result.returncode == 0:
        return None
    ## exit code 1 is findings, which it writes to stderr, 2 a failure of its own
    return result.stderr.strip() or result.stdout.strip() or f"exit code {result.returncode}"


def check(where: Path = ROOT) -> str | None:
    """Why the Markdown files in WHERE fail markdownlint, for scripts/ci, else None."""
    findings = lint(where)
    if findings is None:
        return None
    return f"\n{findings}\n(scripts/ci --fix fixes what markdownlint can, wrap a long line by hand)"


def fix() -> str | None:
    """Fix what markdownlint can in the Markdown files of the repo, for scripts/ci --fix."""
    lint(fix=True)
    return None

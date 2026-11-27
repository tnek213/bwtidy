"""docs/restore.md runs on a stock python3: Python 3.8 or later and its standard library only.

It is for a lost vault, on Linux, macOS or Windows, so it can count on neither bwtidy nor a
package to install. What it does against a real account is test_backup_import.py's.
"""

import ast
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from tests.support.restore_doc import programs

ROOT = Path(__file__).resolve().parents[3]
TEXT = (ROOT / "docs" / "restore.md").read_text()
## the oldest python3 still found on a stock machine (macOS has 3.9 with its developer tools)
OLDEST = (3, 8)
PROGRAMS = programs(TEXT)
SHELL = re.compile(r"^```(sh|powershell)\n(.*?)^```$", re.DOTALL | re.MULTILINE)
PAIR = re.compile(
    r"^Linux, macOS, WSL \(Bash\):\n\n```sh\n(.*?)^```\n\n"
    r"Windows \(PowerShell\):\n\n```powershell\n(.*?)^```$",
    re.DOTALL | re.MULTILINE,
)
PAIRS = PAIR.findall(TEXT)
RUNS = re.compile(r"\bpython3 (\S+\.py)\b")
PS_RUNS = re.compile(r"\bpy (\S+\.py)\b")


def imported(tree: ast.Module) -> set[str]:
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names |= {alias.name.split(".")[0] for alias in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module.split(".")[0])
    return names


def test_the_document_has_its_programs():
    assert set(PROGRAMS) == {"unpack.py", "orgs.py", "attach.py", "show.py", "trash.py", "check.py"}


def test_the_shell_blocks_hold_no_python():
    ## the programs are files of their own, so each one is shown once as Python
    assert "python3 -c" not in TEXT
    assert "<<" not in TEXT


def test_the_shell_blocks_are_paired_with_the_exact_labels():
    assert PAIRS
    assert TEXT.count("Linux, macOS, WSL (Bash):") == len(PAIRS)
    assert TEXT.count("Windows (PowerShell):") == len(PAIRS)
    assert SHELL.findall(TEXT) == [
        block for bash, ps in PAIRS for block in (("sh", bash), ("powershell", ps))
    ]


def test_the_shell_blocks_run_the_same_programs_of_the_page():
    run = set()
    for bash, ps in PAIRS:
        names = RUNS.findall(bash)
        assert PS_RUNS.findall(ps) == names
        run.update(names)
    assert run == set(PROGRAMS)


def test_the_powershell_blocks_avoid_unsupported_commands_and_redirection():
    banned = re.compile(r"&&|\|\||\?\?|\?\.|>|\bpython3\b|\bpython\s+-c\b")
    for _, ps in PAIRS:
        assert not banned.search(ps), ps


@pytest.fixture
def pwsh():
    executable = shutil.which("pwsh")
    if executable is None:
        reason = "pwsh is required to check the restore commands"
        if os.environ.get("CI") == "true":
            pytest.fail(reason)
        pytest.skip(reason)
    return executable


def run_powershell(executable: str, script: str, env: dict[str, str] | None = None) -> None:
    result = subprocess.run(
        [executable, "-NoProfile", "-NonInteractive", "-Command", script],
        capture_output=True,
        text=True,
        timeout=30,
        env=env,
    )
    assert result.returncode == 0, result.stderr or result.stdout


@pytest.mark.parametrize("block", [ps for _, ps in PAIRS], ids=lambda ps: ps.splitlines()[0])
def test_each_powershell_block_parses(pwsh, block):
    run_powershell(
        pwsh,
        f"""$block = @'
{block}'@
$errors = $null
[System.Management.Automation.Language.Parser]::ParseInput(
    $block, [ref]$null, [ref]$errors
) | Out-Null
if ($errors.Count) {{
    $errors | Out-String | Write-Output
    exit 1
}}
""",
    )


def test_powershell_creates_and_cleans_up_the_restore_directory(pwsh, tmp_path):
    setup = PAIRS[0][1].split("Copy-Item", 1)[0]
    cleanup = "Set-Location ~" + PAIRS[-1][1].split("Set-Location ~", 1)[1]
    run_powershell(
        pwsh,
        f"""$ErrorActionPreference = "Stop"
{setup}
if ($env:PYTHONUTF8 -ne "1" -or -not (Test-Path -LiteralPath $D -PathType Container)) {{
    throw "The restore directory or UTF-8 setting is missing"
}}
if ((Split-Path -Parent $D) -ne $env:TEMP) {{
    throw "The restore directory is outside TEMP"
}}
$env:BW_SESSION = "test"
$env:BITWARDENCLI_APPDATA_DIR = Join-Path $D "bw"
{cleanup}
if (Test-Path -LiteralPath $D) {{
    throw "The restore directory still exists"
}}
if ($env:BW_SESSION -or $env:BITWARDENCLI_APPDATA_DIR -or $env:PYTHONUTF8) {{
    throw "A restore environment variable is still set"
}}
""",
        env={**os.environ, "TEMP": str(tmp_path)},
    )
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("name", sorted(PROGRAMS))
def test_each_program_opens_its_files_as_utf_8(name):
    ## Windows opens a file in its code page, PYTHONUTF8 aside
    calls = [
        node
        for node in ast.walk(ast.parse(PROGRAMS[name]))
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "open"
    ]
    assert all(any(k.arg == "encoding" for k in call.keywords) for call in calls), name


@pytest.mark.parametrize("name", sorted(PROGRAMS))
def test_each_program_is_python_3_8(name):
    ast.parse(PROGRAMS[name], feature_version=OLDEST)


@pytest.mark.parametrize("name", sorted(PROGRAMS))
def test_each_program_needs_only_the_standard_library(name):
    tree = ast.parse(PROGRAMS[name])
    assert imported(tree) <= sys.stdlib_module_names


def test_the_document_needs_no_jq_or_unzip():
    ## the old commands of the document, a stock machine lacks them
    words = TEXT.replace("`", " ").split()
    assert "jq" not in words
    assert "unzip" not in words


def test_the_document_says_what_each_system_needs():
    for heading in ("Linux", "macOS", "Windows"):
        assert f"\n### {heading}\n" in TEXT
    commands_needed = (
        "brew install age",
        "winget install --id FiloSottile.age",
        "$PSVersionTable.PSVersion",
        "py --version",
    )
    for needed in commands_needed:
        assert needed in TEXT
    assert "winpty" not in TEXT
    assert "alias python3=" not in TEXT


def test_the_shell_commands_run_in_bash_3_and_without_dev_shm():
    ## macOS has bash 3.2 and no /dev/shm
    for bash4 in ("mapfile", "readarray", "declare -A", "${D,,}", "${D^^}"):
        assert bash4 not in TEXT
    assert "mktemp -d -p" not in TEXT
    assert "PYTHONUTF8=1" in TEXT

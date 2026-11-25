"""The Python of docs/restore.md: the program attach.py and the python3 -c commands.

The document's commands need only a stock python3 (3.8 or later, the standard library alone),
so tests/repo/test_restore_doc.py parses them for that, and the integration test
test_backup_import.py runs attach.py against the dev vault.
"""

import re

## step 5 writes the program with cat and a quoted heredoc, so it is the text up to EOF
ATTACH = re.compile(r"^cat > attach\.py <<'EOF'\n(.*?)^EOF$", re.DOTALL | re.MULTILINE)
## the shell's single quotes, so the Python in them holds none
COMMAND = re.compile(r"python3 -c '([^']*)'")


def attach_program(text: str) -> str:
    """The program of step 5 that pairs the items and uploads the attachments."""
    found = ATTACH.findall(text)
    assert len(found) == 1, "docs/restore.md must write attach.py once"
    return found[0]


def commands(text: str) -> list[str]:
    """The Python of every python3 -c command."""
    return COMMAND.findall(text)

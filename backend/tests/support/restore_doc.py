"""The Python of docs/restore.md: its programs, each a python block titled with its file name.

The document shows each program once in a fence opened as ```python title="NAME.py"``` and
its shell blocks only run them. The programs need only a stock python3 (3.8 or later, the
standard library alone), so tests/repo/test_restore_doc.py parses them for that, and the
integration test test_backup_import.py runs them against the dev vault.
"""

import re

## the fence of a program, up to the fence that closes it
PROGRAM = re.compile(r'^```python title="([^"]+)"\n(.*?)^```$', re.DOTALL | re.MULTILINE)


def programs(text: str) -> dict[str, str]:
    """The source of every program of the document by its title."""
    found = PROGRAM.findall(text)
    titles = [title for title, _ in found]
    twice = sorted({title for title in titles if titles.count(title) > 1})
    assert not twice, f"docs/restore.md shows these programs more than once: {twice}"
    return dict(found)


def attach_program(text: str) -> str:
    """The program of step 5 that pairs the items and uploads the attachments."""
    return programs(text)["attach.py"]

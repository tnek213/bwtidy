"""bw as another device in the app container, and the put-back of the vault after a test wrote."""

import json
import subprocess
from pathlib import Path

from support.api import Api, commit_new_entries, last_entry_id
from support.login import EMAIL, PASSWORD, SERVER

DEV_VAULT = Path(__file__).resolve().parents[4] / "scripts" / "dev-vault"
## the first call of the second CLI state logs in, every call unlocks and syncs
ELSEWHERE_TIMEOUT = 180


def elsewhere(script: str, *args: str) -> str:
    """Run a shell script with bw as another device in the app container, answer its stdout.

    A second CLI state of its own (dev/dev-vault.md, "Trying the auto-commit"), logged in and
    unlocked before the script runs, so the app's session key stays valid. The script gets
    args as $1, $2 and so on. It must print no value of an item, and its stderr is never shown.
    """
    prelude = f"""
export BITWARDENCLI_APPDATA_DIR=/tmp/bwtidy-e2e
if bw status | grep -q '"status":"unauthenticated"'; then
  bw config server {SERVER} >/dev/null
  bw login {EMAIL} --passwordenv BWTIDY_E2E_PASSWORD >/dev/null
fi
BW_SESSION=$(bw unlock --passwordenv BWTIDY_E2E_PASSWORD --raw)
export BW_SESSION
bw sync >/dev/null
"""
    result = subprocess.run(
        [
            str(DEV_VAULT),
            "exec",
            "env",
            f"BWTIDY_E2E_PASSWORD={PASSWORD}",
            "sh",
            "-ec",
            prelude + script,
            "elsewhere",
            *args,
        ],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=ELSEWHERE_TIMEOUT,
    )
    assert result.returncode == 0, f"bw as another device exited with {result.returncode}"
    return result.stdout


## replaces the notes of the item on stdin, the new notes in the environment
SET_NOTES = (
    "import json, os, sys; item = json.load(sys.stdin); "
    "item['notes'] = os.environ['NOTES']; print(json.dumps(item))"
)


def edit_notes_elsewhere(item_id: str, notes: str) -> None:
    """Change an item's notes as another device, which bwtidy sees only after its next sync."""
    elsewhere(
        'bw get item "$1" | NOTES="$2" python3 -c "$3" | bw encode | bw edit item "$1" >/dev/null',
        item_id,
        notes,
        SET_NOTES,
    )


## sets the notes of the item on stdin to the JSON in argv 1 (null for none)
PUT_NOTES = (
    "import json, sys; item = json.load(sys.stdin); "
    "item['notes'] = json.loads(sys.argv[1]); print(json.dumps(item))"
)


def put_notes_back_elsewhere(item_id: str, notes: str | None) -> None:
    """Give the item its notes from before as another device, none when it had none.

    The app sees them at its next sync (a write, or the unlock after file_put_back ended the
    session)."""
    elsewhere(
        'bw get item "$1" | python3 -c "$2" "$3" | bw encode | bw edit item "$1" >/dev/null',
        item_id,
        PUT_NOTES,
        json.dumps(notes),
    )


IDS = "import json, sys; print('\\n'.join(item['id'] for item in json.load(sys.stdin)))"


def trashed_ids() -> set[str]:
    """The ids of the items in the trash, as another device sees them."""
    return set(elsewhere('bw list items --trash | python3 -c "$1"', IDS).split())


## the operations that can make a folder (a restore only makes one the vault lacks)
MAKE_FOLDERS = {"moveToFolder", "createItem", "restoreFolder"}
## the rows that name a folder whose name a put back gives back, from the backup of the first
FOLDER_KINDS = {"renameFolder", "deleteFolder"}

## Deletes for good what a put back leaves in the trash: the items the writes made (argv 1, a
## JSON list) and the items active before (argv 2) that a new item of their name replaced (a
## restore brings an item moved into an organization back as a new one). Then deletes the
## folders no item is in. Prints how many it deleted.
REMOVE_LEFTOVERS = """
import json, subprocess, sys
created, before = set(json.loads(sys.argv[1])), set(json.loads(sys.argv[2]))
def bw(*args):
    return subprocess.run(["bw", *args], check=True, capture_output=True, text=True).stdout
active = json.loads(bw("list", "items"))
trash = json.loads(bw("list", "items", "--trash"))
names = {item["name"] for item in active}
gone = [i for i in trash if i["id"] in created or (i["id"] in before and i["name"] in names)]
for item in gone:
    bw("delete", "item", item["id"], "--permanent")
kept = [item for item in active + trash if item not in gone]
used = {item.get("folderId") for item in kept}
empty = [f["id"] for f in json.loads(bw("list", "folders")) if f["id"] and f["id"] not in used]
for folder in empty:
    bw("delete", "folder", folder)
print(len(gone) + len(empty))
"""


def last_change_id(api: Api) -> int:
    return max((entry["id"] for entry in api.get("/api/change-log")), default=0)


def active_ids(api: Api) -> set[str]:
    return {row["id"] for row in api.get("/api/grid")["rows"]}


def put_back(api: Api, since: int, active_before: set[str]) -> bool:
    """Put every item the change log rows after since wrote back as it was before the first.

    One restore from the oldest row's backup (the vault before the first write) brings each
    item back and trashes the ones the writes made. A folder a row renamed or deleted gets its
    name back from the backup of its first such row (a deleted one is created again). A restore
    cannot undo everything, so what it leaves (the items it trashed or replaced, new folders) is
    then deleted for good as another device. Answers whether it deleted any: the app knows only
    after its next sync. The tool note is left out, its sections are each test's to put back.
    """
    api.wait_saved()
    written = sorted(
        (
            entry
            for entry in api.get("/api/change-log")
            if entry["id"] > since and entry["result"] == "ok" and entry["backup"]
        ),
        key=lambda entry: entry["id"],
    )
    if not written:
        return False
    grid = api.get("/api/grid")
    revisions = {row["id"]: row["revisionDate"] for row in grid["rows"]}
    item_ids = [
        item_id
        for entry in written
        for item_id in dict.fromkeys([*entry["itemIds"], *entry["createdItemIds"]])
        if item_id != grid["toolNoteId"]
    ]
    item_ids = list(dict.fromkeys(item_ids))
    if item_ids:
        operation = {
            "kind": "restoreItems",
            "backup": written[0]["backup"],
            "itemIds": item_ids,
            "revisionDates": {i: revisions[i] for i in item_ids if i in revisions},
        }
        after = last_entry_id(api)
        api.post("/api/queue", {"operations": [operation]})
        commit_new_entries(api, after)
    folders: dict[str, str] = {}
    for entry in written:
        if entry["operationKind"] in FOLDER_KINDS and entry.get("folderId"):
            folders.setdefault(entry["folderId"], entry["backup"])
    if folders:
        restores = [
            {"kind": "restoreFolder", "backup": backup, "folderId": folder_id}
            for folder_id, backup in folders.items()
        ]
        after = last_entry_id(api)
        api.post("/api/queue", {"operations": restores})
        commit_new_entries(api, after, len(restores))
    rows = [entry for entry in api.get("/api/change-log") if entry["id"] > since]
    created = sorted({item_id for entry in rows for item_id in entry["createdItemIds"]})
    replaced = active_before - active_ids(api)
    if not created and not replaced and not MAKE_FOLDERS & {e["operationKind"] for e in rows}:
        return False
    removed = elsewhere(
        'python3 -c "$1" "$2" "$3"',
        REMOVE_LEFTOVERS,
        json.dumps(created),
        json.dumps(sorted(active_before)),
    )
    return int(removed) > 0

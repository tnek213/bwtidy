# Restoring a vault from a bwtidy backup

Written for bwtidy COMMIT. The project: https://github.com/tnek213/bwtidy

This is for when the vault itself is gone or badly broken: the account is
lost, most items are deleted, or you move to a new account. It imports a
backup's exports into an empty account and puts the attachments back. Every
item comes back, but as a new item with a new id (see What does not come
back).

> [!TIP]
> When only a bwtidy change went wrong and the vault is otherwise fine, use
> Restore on that change's row in bwtidy's Change log instead.

Every step is here, nothing of bwtidy is needed: only `age` (or `rage`) from
any distribution, Python 3 (3.8 or later, with nothing but its standard
library), the official Bitwarden CLI and a bash to run the commands in. Every
backup holds this file as `RESTORE.md`, the repository has it as
`docs/restore.md`.

## What you need on your system

The commands are written for bash and work the same on every system once
these four are installed. Check them with `age --version`, `python3 --version`,
`bw --version` and `bash --version`.

### Linux

- `age` from your distribution (`apt install age`, `dnf install age`,
  `pacman -S age` and so on, the
  [age page](https://github.com/FiloSottile/age#installation) lists them) or a
  release binary.
- `python3`, usually there already.
- The Bitwarden CLI: step 3 installs it from npm, or take the
  [standalone build](https://bitwarden.com/download/).
- bash, there already.

### macOS

- `age`: `brew install age` or a release binary from the
  [age page](https://github.com/FiloSottile/age#installation).
- `python3`: the one that comes with the Xcode command line tools
  (`xcode-select --install`), or one from [python.org](https://www.python.org/downloads/)
  or Homebrew.
- The Bitwarden CLI: step 3 installs it from npm (Node.js from
  [nodejs.org](https://nodejs.org/) or Homebrew), or take the
  [standalone build](https://bitwarden.com/download/). Homebrew's
  `bitwarden-cli` also works, it is not Bitwarden's own package.
- bash: the system's bash 3.2 is enough, no command here needs bash 4.
  Terminal's default shell is zsh, so run `bash` first, or paste the
  commands into zsh, which takes them as they are.

### Windows

- bash: either Git Bash, which comes with
  [Git for Windows](https://git-scm.com/download/win), or WSL. In WSL
  everything is as on Linux, install the four there and skip the rest of this
  list.
- Python from [python.org](https://www.python.org/downloads/windows/) or the
  Microsoft Store. There the command is `python` or `py`, not `python3`: put
  `alias python3=python` before the first command in Git Bash (a shell
  alias, only for this window), or replace `python3` in the commands.
- `age`: `winget install --id FiloSottile.age`,
  `scoop bucket add extras && scoop install age`, or the Windows release
  binary from the [age page](https://github.com/FiloSottile/age#installation)
  (put `age.exe` on the `PATH`).
- The Bitwarden CLI: the standalone `bw.exe` from the
  [download page](https://bitwarden.com/download/),
  `choco install bitwarden-cli` or `npm install -g @bitwarden/cli`, as the
  [CLI documentation](https://bitwarden.com/help/cli/) says. Step 3 installs
  it from npm as well, in Git Bash that works too.
- Step 1 sets `PYTHONUTF8=1` so that Python reads and writes UTF-8, as
  it does on Linux, and not the Windows code page.
- `age` asks for the passphrase on the terminal. Git Bash's default
  terminal (mintty) is not a Windows console, and a program that reads the
  console directly may not see it: if `age -d` fails to ask or fails right
  away, start it as `winpty age -d ...` or run the command in Windows
  Terminal or PowerShell.
- Tmpfs: Windows and macOS have no `/dev/shm`. Step 1 then uses the
  temporary folder, which is on the disk, so the decrypted files are written
  there until step 7 deletes them.

## What a backup holds

```text
backup.zip                    the backup FILE.zip.age, decrypted in step 1
├── personal-export.zip       data.json (bw import takes it) and attachments folders not to use
├── org-<orgId>-export.json   one per organization
├── item-attachments/
│   └── <itemId>/
│       └── <fileName>        every attachment by the id of its item
├── raw/
│   ├── items.json            every active item as bwtidy saw it
│   ├── trash.json            the trash, which no export holds
│   ├── folders.json
│   ├── collections.json
│   └── organizations.json
└── RESTORE.md                this file
```

The `attachments` folders in `personal-export.zip` are filed by item name and
lose files of items with the same name. Take attachments from
`item-attachments` only. A second attachment with the file name of another on
the same item is stored as `<attachmentId>-<fileName>`.

## 1. Decrypt the backup on a tmpfs

The decrypted files hold every secret in plain text, so they stay in RAM
(`/dev/shm`, where the system has it) and are deleted at the end (step 7).
Pick the newest backup from
before the loss. bwtidy keeps them as `HOST/EMAIL/<UTC timestamp>.zip.age`
under its backups mount (`/backups` in the container: the volume
`bwtidy-backups` or the host directory `BWTIDY_BACKUPS` names), where
`HOST` is the server (`vault.bitwarden.com` for the default one) and `EMAIL`
the account's email in lowercase. A copy from a backup tool
works the same.

`age` asks for the passphrase at a terminal. It is the master password
bwtidy last logged in with, since a login with a new master password
re-encrypts every backup. A copy made before a master password change, or a
re-encryption that was cut off, takes an earlier one: the secure note named
`bwtidy` lists them in its hidden fields `backup-password` and `older-backup-passwords`,
also in any backup that opens (the block after the next prints them).

```sh
export PYTHONUTF8=1
TMPFS=/dev/shm
[ -d "$TMPFS" ] || TMPFS=${TMPDIR:-/tmp}
D=$(mktemp -d "$TMPFS/bwtidy-restore.XXXXXX")
cd "$D"
cp /path/to/FILE.zip.age .
age -d -o backup.zip FILE.zip.age && rm FILE.zip.age
python3 -m zipfile -e backup.zip . && rm backup.zip
python3 -c 'import zipfile; zipfile.ZipFile("personal-export.zip").extract("data.json")'
python3 -c '
import json
for name in "raw/items.json", "raw/trash.json":
    print(len(json.load(open(name))), "items in", name)
for org in json.load(open("raw/organizations.json")):
    print("organization", org["id"], org["name"])'
```

The fields of the `bwtidy` note, with the passphrases of older backups:

```sh
python3 -c '
import json
for item in json.load(open("raw/items.json")):
    if item["name"] == "bwtidy":
        print(json.dumps(item.get("fields"), indent=2))'
```

## 2. The account to import into

> [!WARNING]
> Import into an empty account: a new one, or the old one after Settings, My
> account, Purge vault. An import into a vault with items adds the backup's
> items next to them, it does not replace anything.

Organization items go into organizations you own, one per
`org-<orgId>-export.json`, each with the name it had in the backup (step 1
printed them). The CLI cannot create an organization: create the missing ones
in the web vault, and rename an existing one if its name changed. Step 5 finds
an item's organization by its name, so the names must also be unique, in the
backup and in the account.

## 3. A Bitwarden CLI of its own

Take the official CLI, either the native executable from
https://bitwarden.com/download/ (Command line interface, put `bw` on your
`PATH`) or the npm package in a directory the run makes (it holds no secrets,
so it may go on the disk). bwtidy used this version:

```sh
CLI=$(mktemp -d)
npm install --prefix "$CLI" --no-fund --no-audit @bitwarden/cli@2026.10.0
export PATH="$CLI/node_modules/.bin:$PATH"
```

A native `bw` on the `PATH` needs none of this, and then `CLI` stays unset.

Its state (`BITWARDENCLI_APPDATA_DIR`) goes into `$D` as well, never the CLI
state anything else on the machine uses. Then log in to the account of step 2.
`bw config server` takes the `HOST` of step 1 as a URL (a self-hosted server
by its own URL), and bitwarden.com mails a one-time code for a new device:

```sh
export BITWARDENCLI_APPDATA_DIR="$D/bw"
bw config server https://vault.bitwarden.com
bw login EMAIL
export BW_SESSION=$(bw unlock --raw)
```

## 4. Import the exports

bw has no zip format, so the personal export goes in as its `data.json`, in
the format `bitwardenjson`:

```sh
bw import bitwardenjson data.json
```

Each organization export goes into the organization of the same name. Step 1
printed the old ids (OLDID) with their names, this prints the new ones
(NEWID). Run the import once per organization:

```sh
bw list organizations | python3 -c '
import json, sys
for org in json.load(sys.stdin):
    print(org["id"], org["name"])'
bw import bitwardenjson org-OLDID-export.json --organizationid NEWID
```

## 5. The attachments

The import gives every item a new id, so each backup item has to be found
again among the imported items to get the files under
`item-attachments/<old id>/`. It is found by its content: two items match
when they have the same type, name and every other value (notes, login,
fields, password history, passkeys and so on, leaving out ids and dates),
the same folder by name, the same collections by name and the same
organization by name (or both personal). Items that are equal in all of that
are taken in order: whichever gets which file, the vault is the same. A
backup item that matches nothing gets the one imported item of its type,
name and organization, if there is exactly one.

First write the program that pairs the items and uploads the attachments. bw
names an attachment after its file, so the program copies each file to its
real file name under `upload/` first:

```sh
cat > attach.py <<'EOF'
import json
import os
import shutil
import subprocess
import sys

# what an import makes anew, and what the key compares by name instead
LEFT_OUT = {"id", "revisionDate", "creationDate", "deletedDate", "organizationId",
            "collectionIds", "folderId", "attachments"}


# the full path finds bw.cmd of an npm install on Windows too
BW = shutil.which("bw") or "bw"


def bw(*args):
    return json.loads(subprocess.run([BW, *args], check=True, stdout=subprocess.PIPE).stdout)


def load(path):
    with open(path, encoding="utf-8") as file:
        return json.load(file)


def names(listing):
    return {entry["id"]: entry["name"] for entry in listing if entry.get("id")}


def leaves(value, path=()):
    """Every value below value that is neither an object nor an array, with its path."""
    if isinstance(value, dict):
        for key, child in value.items():
            yield from leaves(child, path + (key,))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from leaves(child, path + (index,))
    elif value is not None:
        yield [path, value]


class Names:
    """Folder, collection and organization names by id, of the backup or of the account."""

    def __init__(self, where, folders, collections, organizations):
        org_names = [org["name"] for org in organizations]
        if len(set(org_names)) != len(org_names):
            sys.exit(f"{where} has organizations of the same name, give each a name of its own")
        self.folders = names(folders)
        self.collections = names(collections)
        self.organizations = names(organizations)

    def place(self, item):
        org = item.get("organizationId")
        return [item.get("type"), item.get("name"),
                (self.organizations.get(org) or f"id {org}") if org else None]

    def key(self, item):
        folder = item.get("folderId")
        content = {k: v for k, v in item.items() if k not in LEFT_OUT}
        return json.dumps(self.place(item) + [
            self.folders.get(folder) if folder else None,
            sorted(json.dumps(self.collections.get(c)) for c in item.get("collectionIds") or []),
            sorted(json.dumps(leaf) for leaf in leaves(content)),
        ])


def members(item):
    """Each attachment's id, file name and file in item-attachments/<item id>/."""
    used = set()
    for attachment in item.get("attachments") or []:
        member = attachment["fileName"]
        while member in used:
            member = f"{attachment['id']}-{member}"
        used.add(member)
        yield attachment["id"], attachment["fileName"], member


old = load(sys.argv[1])
old_names = Names("the backup", load("raw/folders.json"), load("raw/collections.json"),
                  load("raw/organizations.json"))
subprocess.run([BW, "sync"], check=True, stdout=subprocess.DEVNULL)
new = bw("list", "items")
new_names = Names("the account", bw("list", "folders"), bw("list", "collections"),
                  bw("list", "organizations"))

# the same content: the first unused imported item with the same key
pool = {}
for item in new:
    pool.setdefault(new_names.key(item), []).append(item)
pairs = {}
used = set()
for item in old:
    same = pool.get(old_names.key(item))
    if same:
        pairs[item["id"]] = same.pop(0)
        used.add(pairs[item["id"]]["id"])
# else the one unused imported item of its type, name and organization
for item in old:
    if item["id"] not in pairs:
        place = old_names.place(item)
        fits = [n for n in new if n["id"] not in used and new_names.place(n) == place]
        if len(fits) == 1:
            pairs[item["id"]] = fits[0]
            used.add(fits[0]["id"])

for item in old:
    target = pairs.get(item["id"])
    there = [a["fileName"] for a in (target or {}).get("attachments") or []]
    for attachment, file_name, member in members(item):
        said = f"attachment {attachment} of backup item {item['id']}"
        source = os.path.join("item-attachments", item["id"], member)
        if target is None:
            print(f"no imported item fits backup item {item['id']}, "
                  f"attachment {attachment} is not uploaded", flush=True)
        elif file_name in there:
            there.remove(file_name)
        elif not os.path.isfile(source):
            print(f"{said} has no file in the backup, it is not uploaded", flush=True)
        else:
            os.makedirs(os.path.join("upload", attachment), exist_ok=True)
            upload = os.path.join("upload", attachment, file_name)
            shutil.copyfile(source, upload)
            done = subprocess.run([BW, "create", "attachment", "--file", upload,
                                   "--itemid", target["id"]], stdout=subprocess.DEVNULL)
            if done.returncode == 0:
                print(f"{said} is on item {target['id']}", flush=True)
shutil.rmtree("upload", ignore_errors=True)
EOF
```

Then run it on the backup items of `raw/items.json`. It syncs bw, lists what
the account has now and prints a line for each attachment it uploads or
cannot upload:

```sh
python3 attach.py raw/items.json
```

A failed upload prints bw's error. Run the program again: the attachments
already there (an attachment of that file name on the item) are skipped, so
it only adds what is missing.

The program stops at once with "has organizations of the same name" when two
organizations share a name in the backup or the account (`raw/organizations.json`
or `bw list organizations`): give each a name of its own in the account,
and for the backup put the new name into `raw/organizations.json` for that
id too. Then run it again.

Each "no imported item fits" line is a backup item whose content no imported
item has and whose type, name and organization do not tell one. When every
item of an organization is printed so, check that the organization has the
name it had in the backup (step 2). Otherwise upload by hand. This prints the
old item (OLDID), from `with-trash.json` instead for an item of the trash
(step 6):

```sh
python3 -c '
import json, sys
for item in json.load(open(sys.argv[1])):
    if item["id"] == sys.argv[2]:
        print(json.dumps(item, indent=2))' raw/items.json OLDID
```

Its files are under `item-attachments/OLDID/` (the attachment ids and file
names are in the old item), `bw list items --search NAME` lists the new
candidates (also those whose name only contains NAME), compare their values,
and `bw create attachment --file FILE --itemid NEWID` with the file copied to
its real name first.

Each "has no file in the backup" line means the zip is damaged or was
unpacked only in part: decrypt and unpack it again (step 1) and run the
program again.

## 6. The trash, only if you want it

No export holds the trash, only `raw/trash.json`. bw import refuses or would
take over some of its keys, so drop them first and import the rest into the
personal vault:

```sh
python3 -c '
import json
trash = json.load(open("raw/trash.json"))
dropped = ("deletedDate", "folderId", "organizationId", "collectionIds")
moved = [{k: v for k, v in item.items() if k not in dropped} for item in trash]
with open("trash-import.json", "w") as file:
    items = [{k: v for k, v in item.items() if k != "id"} for item in moved]
    json.dump({"encrypted": False, "items": items}, file)
with open("with-trash.json", "w") as file:
    json.dump(json.load(open("raw/items.json")) + moved, file)'
bw import bitwardenjson trash-import.json
python3 attach.py with-trash.json
```

`with-trash.json` is the active items and the trashed ones (with their old
ids), so the program of step 5 finds the trashed items as well. The
attachments of the active items are already there, so it only uploads those
of the trashed items. They come back as active personal items in no folder.
Move them to the trash in the web vault if that is where they belong.

## 7. Check and clean up

The account must have as many items and attachments as the backup: the two
lines the block below prints must have the same numbers. Each "not uploaded"
line of step 5 is one attachment missing. With the trash imported (step 6),
`with-trash.json` takes the place of `raw/items.json`: set
`ITEMS=with-trash.json` instead of the first line below.

```sh
ITEMS=raw/items.json
bw sync
bw list items | python3 -c '
import json, sys
def count(items):
    return len(items), sum(len(item.get("attachments") or []) for item in items)
print("the account: %d items, %d attachments" % count(json.load(sys.stdin)))
print("the backup:  %d items, %d attachments" % count(json.load(open(sys.argv[1]))))' "$ITEMS"
bw logout
cd ~ && rm -rf "$D" ${CLI:+"$CLI"}
unset BW_SESSION BITWARDENCLI_APPDATA_DIR
```

Then log bwtidy in to the restored account (Log out, with "Also log out of
Bitwarden" checked if the account changed).

## What does not come back

The test `backend/tests/integration/test_backup_import.py` in the repository
checks this on the dev vault:

- Every id is new: items, attachments, folders and collections. bwtidy's
  Change log names the old ids, so its Restore buttons cannot find the
  restored items.
- `creationDate` and `revisionDate` are new.
- Organization items land in the organization you import into (the one of
  the same name, step 4), in its collections of the same names. Its members,
  groups and policies are not in the backup.
- The trash comes back only as active items, without its deletion dates.

Everything else comes back as it was: every value of every item (custom
fields with duplicate names and trailing spaces included), passkeys,
password history, favorites, master password reprompt, folders and
collections by name, which item is in which folder and collections, and
every attachment with its file name and bytes.

Not in a backup at all: Sends, account settings (two-step login, emergency
access, devices) and organization settings.

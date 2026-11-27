# Restore a vault from a backup

Written for bwtidy COMMIT. The project is at <https://github.com/tnek213/bwtidy>.

This page is for when the vault itself is gone or badly broken. The account
may be lost or most items deleted, or you may be moving to a new account. You
import a backup's exports into an empty account and put the attachments back.
Every item comes back as a new item with a new id (see
[What does not come back](#what-does-not-come-back)).

> [!TIP]
> When only a bwtidy change went wrong and the vault is otherwise fine, use
> Restore on that change's row in bwtidy's Change log instead.

Every step is on this page and none of them needs bwtidy. You need `age` (or
`rage`) from any distribution and Python 3.8 or later with nothing but its
standard library. You also need the official Bitwarden CLI and a bash to run
the commands in. Every backup holds this page as `RESTORE.md`, and the
repository has it as `docs/restore.md`.

## What you need on your system

The commands are written for bash. They work the same on every system once
these four tools are installed. To check them, run `age --version` and
`python3 --version` as well as `bw --version` and `bash --version`.

### Linux

- `age` from your distribution or as a release binary. It is
  `apt install age` on Debian or Ubuntu and `dnf install age` on Fedora.
  `pacman -S age` installs it on Arch, and the
  [age page](https://github.com/FiloSottile/age#installation) lists the rest.
- `python3`, which is usually there already.
- The Bitwarden CLI, which step 3 installs from npm. You can also take the
  [standalone build](https://bitwarden.com/download/).
- bash, which is there already.

### macOS

- `age` from `brew install age` or as a release binary from the
  [age page](https://github.com/FiloSottile/age#installation).
- `python3` from the Xcode command line tools (`xcode-select --install`).
  One from [python.org](https://www.python.org/downloads/) or Homebrew works
  too.
- The Bitwarden CLI, which step 3 installs from npm. Node.js for it comes
  from [nodejs.org](https://nodejs.org/) or Homebrew. You can also take the
  [standalone build](https://bitwarden.com/download/). Homebrew's
  `bitwarden-cli` works too, although it is not Bitwarden's own package.
- bash. The system's bash 3.2 is enough because no command here needs bash
  4. The default shell of Terminal is zsh, so run `bash` first. You can also
  paste the commands into zsh, which takes them as they are.

### Windows

- bash, either Git Bash from
  [Git for Windows](https://git-scm.com/download/win) or WSL. In WSL
  everything works as on Linux. Install the four tools there and skip the
  rest of this list.
- Python from [python.org](https://www.python.org/downloads/windows/) or the
  Microsoft Store. Its command there is `python` or `py` rather than
  `python3`. Either put `alias python3=python` before the first command in
  Git Bash or replace `python3` in the commands. The alias is a shell alias
  for this window only.
- `age` from `winget install --id FiloSottile.age` or
  `scoop bucket add extras && scoop install age`. You can also take the
  Windows release binary from the
  [age page](https://github.com/FiloSottile/age#installation) and put
  `age.exe` on the `PATH`.
- The Bitwarden CLI in one of the three ways the
  [CLI documentation](https://bitwarden.com/help/cli/) names. They are the
  standalone `bw.exe` from the [download page](https://bitwarden.com/download/),
  `choco install bitwarden-cli` and `npm install -g @bitwarden/cli`. Step 3
  installs it from npm, which works in Git Bash too.
- Step 1 sets `PYTHONUTF8=1` so that Python reads and writes UTF-8 as it does
  on Linux. Without it Python would use the Windows code page.
- `age` asks for the passphrase on the terminal. Git Bash's default terminal
  (mintty) is not a Windows console. A program that reads the console
  directly may then not see what you type. If `age -d` does not ask or fails
  right away, start it as `winpty age -d ...` or run the command in Windows
  Terminal or PowerShell.
- Windows and macOS have no tmpfs at `/dev/shm`. Step 1 then uses the
  temporary folder instead, which is on the disk. The decrypted files stay
  there until step 7 deletes them.

## What a backup holds

A decrypted backup holds these files.

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

The `attachments` folders in `personal-export.zip` are filed by item name, so
they lose the files of items with the same name. Take attachments from
`item-attachments` only. When an item has two attachments with the same file
name, the second one is stored as `<attachmentId>-<fileName>`.

## 1. Decrypt the backup on a tmpfs

The decrypted files hold every secret in plain text. They therefore stay in
RAM (`/dev/shm` where the system has it) and step 7 deletes them at the end.

Pick the newest backup from before the loss. bwtidy keeps each one as
`HOST/EMAIL/<UTC timestamp>.zip.age` under its backups mount. `HOST` is the
server (`vault.bitwarden.com` for the default one) and `EMAIL` is the
account's email in lowercase. The mount is `/backups` in the container,
which is the volume `bwtidy-backups` or the host directory that
`BWTIDY_BACKUPS` names. A copy from a backup tool works the same.

`age` asks for the passphrase at a terminal. It is the master password bwtidy
last logged in with, since a login with a new master password re-encrypts
every backup under its backups mount. A backup keeps an earlier one when it
was copied elsewhere before the change or its re-encryption was cut off. So
does a backup that bwtidy could not open at that login. The bwtidy note lists
these passphrases in its hidden fields `backup-password` and
`older-backup-passwords`. It is a secure note named `bwtidy` unless you
renamed it. The note keeps an earlier password only until the re-encryption
is done, so for an older copy you may need to remember it yourself. Every
backup that opens holds the note too. The second block below prints these
two fields.

The first block makes the directory for the decrypted files and decrypts and
unpacks the backup there. Put the path of your backup in place of
`/path/to/FILE.zip.age` and its file name in place of `FILE.zip.age`. At the
end the block prints how many items the backup holds and the id and name of
each organization.

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

This prints the two fields of the bwtidy note with the note's name. It finds
the note as a personal secure note with the field `backup-password`, so a
renamed note shows too.

```sh
python3 -c '
import json
wanted = ("backup-password", "older-backup-passwords")
for item in json.load(open("raw/items.json")):
    if item.get("type") != 2 or item.get("organizationId"):
        continue
    fields = [f for f in item.get("fields") or [] if f.get("name") in wanted]
    if any(f["name"] == "backup-password" for f in fields):
        print(item["name"])
        print(json.dumps(fields, indent=2))'
```

## 2. Prepare an empty account

> [!WARNING]
> Import into an empty account. That is a new account or the old one emptied
> with Purge vault on the My account page of Settings. An import into a vault
> that has items adds the backup's items next to them and replaces nothing.

Organization items go into organizations you own. Each
`org-<orgId>-export.json` needs one with the name it had in the backup, which
step 1 printed. Each of these organizations must be empty as well. The CLI
cannot create an organization. Create the missing ones in the web vault and
rename an existing one whose name changed. Step 5 finds an item's
organization by its name, so the names must also be unique in the backup and
in the account.

## 3. Set up a Bitwarden CLI of its own

Take the official CLI as the native executable or as the npm package. The
native one is under Command line interface at <https://bitwarden.com/download/>
and goes on your `PATH`. The npm package goes into a directory of its own,
which may be on the disk since it holds no secrets. These commands install
the version that bwtidy uses.

```sh
CLI=$(mktemp -d)
npm install --prefix "$CLI" --no-fund --no-audit @bitwarden/cli@2026.10.0
export PATH="$CLI/node_modules/.bin:$PATH"
```

With a native `bw` on the `PATH` you skip this block and leave `CLI` unset.

The CLI keeps its state (`BITWARDENCLI_APPDATA_DIR`) in `$D` as well, apart
from any CLI state that something else on the machine uses. Then log in to
the account of step 2. `bw config server` takes the `HOST` of step 1 as a
URL, and a self-hosted server goes by its own URL. For a new device
bitwarden.com mails you a one-time code.

```sh
export BITWARDENCLI_APPDATA_DIR="$D/bw"
bw config server https://vault.bitwarden.com
bw login EMAIL
export BW_SESSION=$(bw unlock --raw)
```

## 4. Import the exports

bw import has no zip format, so the personal export goes in as its
`data.json` in the format `bitwardenjson`.

```sh
bw import bitwardenjson data.json
```

Each organization export goes into the organization of the same name. Step 1
printed the old ids (OLDID) with their names, and the first command below
prints the new ones (NEWID). Run the import once for each organization with
its two ids.

```sh
bw list organizations | python3 -c '
import json, sys
for org in json.load(sys.stdin):
    print(org["id"], org["name"])'
bw import bitwardenjson org-OLDID-export.json --organizationid NEWID
```

## 5. Upload the attachments and set organization folders

The import gives every item a new id. To get the files under
`item-attachments/<old id>/` onto the right item, each backup item has to be
found again among the imported items. It is found by its content. A backup
item and an imported item match when all of these are the same:

- the type and the name
- every other value except the item's id and dates and attachments. That
  covers the login and notes as well as fields, password history and passkeys.
- the folder and the collections, compared by name
- the organization, compared by name (or both items are personal)

An organization export holds no folder and no favorite flag, as both belong
to each user and not to the organization. For an organization item the match
leaves them out.

Items that are equal in all of that are taken in order. It does not matter
which of them gets which file, as the vault comes out the same. A backup
item that matched no unused imported item gets a second try once every item
has had its first. It then gets the one unused imported item of its type,
name and organization if there is exactly one.

Each paired organization item then gets the folder and the favorite flag it
had in the backup. The program finds the folder by its name among the
account's folders and saves both with `bw edit item`. An item that already
has them is left as it is.

```graphviz alt="attach.py first pairs every backup item it can with an unused imported item of the same content. For each backup item left it then takes the one unused imported item of its type, name and organization. With none or several it prints for each attachment that no imported item fits. A paired organization item gets the folder of the same name and the favorite flag it had in the backup unless it already has them. A folder name the account lacks prints a line. Then for each attachment of a paired item it skips a file name the imported item already has. It prints a line when the backup lacks the file and otherwise uploads the file, where a failure prints the error of bw."
digraph {
  nodesep=0.3
  ranksep=0.3
  fontname="sans-serif"
  node [fontname="sans-serif" fontsize=13 shape=box style="rounded,filled" fillcolor="#ffffff" color="#5c6bc0" margin="0.12,0.06"]
  edge [fontname="sans-serif" fontsize=12 color="#455a64"]
  item [label="A backup item" fillcolor="#e8eaf6"]
  content [label="First, the same content as\nan unused imported item?" fillcolor="#fff8e1" color="#f9a825"]
  place [label="Then for the rest, exactly\none unused imported\nitem of its type, name\nand organization?" fillcolor="#fff8e1" color="#f9a825"]
  paired [label="Paired with\nthat imported item" fillcolor="#f1f8e9" color="#558b2f"]
  org [label="An organization item\nwithout its folder and\nfavorite of the backup?" fillcolor="#fff8e1" color="#f9a825"]
  edit [label="Sets both with\nbw edit item (prints a line\nwhen the account has no\nfolder of that name)" fillcolor="#f1f8e9" color="#558b2f"]
  nofit [label="Prints \"no imported\nitem fits\" for each\nof its attachments" fillcolor="#ffebee" color="#c62828"]
  there [label="For each attachment,\na file of that name already\non the imported item?" fillcolor="#fff8e1" color="#f9a825"]
  skip [label="Skipped"]
  file [label="Its file in\nitem-attachments?" fillcolor="#fff8e1" color="#f9a825"]
  nofile [label="Prints \"has no file\nin the backup\"" fillcolor="#ffebee" color="#c62828"]
  upload [label="Uploads it with\nbw create attachment\n(a failure prints\nthe error of bw)" fillcolor="#f1f8e9" color="#558b2f"]
  item -> content
  content -> paired [label=" yes"]
  content -> place [label=" no"]
  place -> paired [label=" yes"]
  place -> nofit [label=" no"]
  paired -> org
  org -> edit [label=" yes"]
  org -> there [label=" no"]
  edit -> there
  there -> skip [label=" yes"]
  there -> file [label=" no"]
  file -> upload [label=" yes"]
  file -> nofile [label=" no"]
}
```

First write the program that pairs the items, sets the organization items'
folders and favorites and uploads the attachments. bw names an attachment
after its file, so the program first copies each file to its real file name
under `upload/`.

```sh
cat > attach.py <<'EOF'
import base64
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
        # an organization export holds neither the folder nor the favorite flag
        personal = not item.get("organizationId")
        folder = item.get("folderId") if personal else None
        content = {k: v for k, v in item.items()
                   if k not in LEFT_OUT and (personal or k != "favorite")}
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

# an organization item's folder and favorite flag by the backup, as the import sets neither
folder_ids = {}
for folder_id, folder_name in new_names.folders.items():
    folder_ids.setdefault(folder_name, folder_id)
for item in old:
    target = pairs.get(item["id"])
    if target is None or not item.get("organizationId"):
        continue
    folder = item.get("folderId")
    wanted = {"folderId": None, "favorite": bool(item.get("favorite"))}
    if folder:
        wanted["folderId"] = folder_ids.get(old_names.folders.get(folder))
        if wanted["folderId"] is None:
            print(f"the account has no folder named as the one of backup item {item['id']}, "
                  f"item {target['id']} stays in its folder", flush=True)
            wanted["folderId"] = target.get("folderId")
    if (target.get("folderId") == wanted["folderId"]
            and bool(target.get("favorite")) == wanted["favorite"]):
        continue
    edited = dict(bw("get", "item", target["id"]), **wanted)
    encoded = base64.b64encode(json.dumps(edited).encode("utf-8"))
    # bw reads the encoded item from stdin, as a long one exceeds the Windows command line
    done = subprocess.run([BW, "edit", "item", target["id"]], input=encoded,
                          stdout=subprocess.DEVNULL)
    if done.returncode == 0:
        print(f"item {target['id']} has the folder and favorite of backup item {item['id']}",
              flush=True)

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

Then run it on the backup items of `raw/items.json`. It syncs bw and lists
what the account has now. It prints a line for each organization item whose
folder or favorite it sets. It also prints one for each attachment it uploads
or cannot upload.

```sh
python3 attach.py raw/items.json
```

A failed upload or `bw edit item` prints bw's error. Then run the program
again. It skips each attachment that is already there (one of that file name
on the item) and each item that has its folder and favorite. It adds only what
is missing.

The program stops at once with "has organizations of the same name" when two
organizations share a name. That can be in the backup (`raw/organizations.json`)
or in the account (`bw list organizations`). Give each one a name of its own
in the account. For the backup put the new name into `raw/organizations.json`
under that id as well. Then run the program again.

Each "no imported item fits" line is an attachment of a backup item that
matches nothing. No imported item has its content and no single unused one
has its type, name and organization.

When every item of an organization gets such a line, check that the
organization has the name it had in the backup (step 2). Otherwise
upload the attachments by hand.

This prints the old item (OLDID). For an item of the trash put
`with-trash.json` of step 6 in place of `raw/items.json`.

```sh
python3 -c '
import json, sys
for item in json.load(open(sys.argv[1])):
    if item["id"] == sys.argv[2]:
        print(json.dumps(item, indent=2))' raw/items.json OLDID
```

Its files are under `item-attachments/OLDID/`, and the old item lists their
attachment ids and file names. `bw list items --search NAME` lists the new
candidates, which include those whose name only contains NAME. Compare their
values to find the right one. Copy each file to its real file name and upload
it with `bw create attachment --file FILE --itemid NEWID`.

Each "has no folder named as" line is an organization item whose folder in
the backup has a name no folder of the account has. The item keeps the folder
it is in. Create the folder in the web vault and run the program again.

Each "has no file in the backup" line means that the zip is damaged or was
unpacked only in part. Decrypt and unpack it again (step 1) and run the
program once more.

## 6. Import the trash if you want it

No export holds the trash. It is only in `raw/trash.json`. bw import refuses
some of a trashed item's keys or would take them over, so the commands below
drop them first. They import the rest into the personal vault and then run
the program of step 5 again.

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

`with-trash.json` holds the active items and the trashed ones with their old
ids, so the program of step 5 finds the trashed items as well. The
attachments of the active items are already there and only those of the
trashed items get uploaded. The trashed items come back as active personal
items in no folder. Move them to the trash in the web vault if that is where
they belong.

## 7. Check and clean up

The account must now have as many items and attachments as the backup. The
block below prints one line for the account and one for the backup, and the
two must show the same numbers. Each "not uploaded" line of step 5 is one
missing attachment. If you imported the trash in step 6, `with-trash.json`
takes the place of `raw/items.json`. Then set `ITEMS=with-trash.json` in
place of the first line below.

After the count the block logs bw out. It deletes `$D` with every decrypted
file and the npm CLI of step 3.

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

Then log bwtidy in to the restored account. If the account changed, first use
Log out in bwtidy with "Also log out of Bitwarden" checked.

## What does not come back

The test `backend/tests/integration/test_backup_import.py` in the repository
checks this on the dev vault:

- Every id is new, for items and attachments as well as folders and
  collections. bwtidy's Change log names the old ids, so its Restore buttons
  cannot find the restored items.
- `creationDate` and `revisionDate` are new.
- Organization items land in the organization you import them into, which is
  the one of the same name (step 4). They go into its collections of the same
  names. Its members, groups and policies are not in the backup.
- The trash comes back only as active items without its deletion dates.

Everything else comes back as it was:

- every value of every item, including custom fields of every type and with
  duplicate names or trailing spaces
- passkeys and password history
- favorites and master password reprompt
- folders and collections by name, the empty ones too
- which item is in which folder and collections. Step 5 puts back the folders
  and favorites of organization items, which no export holds.
- every attachment with its file name and bytes

A backup holds no Sends and no organization settings. It also leaves out
account settings such as two-step login, emergency access and devices.

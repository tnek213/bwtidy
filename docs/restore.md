# Restoring a vault from a bwtidy backup

Written for bwtidy COMMIT. The project: https://github.com/tnek213/bwtidy

This is for when the vault itself is gone or badly broken: the account is
lost, most items are deleted, or you move to a new account. It imports a
backup's exports into an empty account and puts the attachments back. Every
item comes back, but as a new item with a new id (see What does not come
back). When only a bwtidy change went wrong and the vault is otherwise fine,
use Restore on that change's row in bwtidy's Change log instead.

Every step is here, nothing of bwtidy is needed: only `age` (or `rage`),
`unzip` and `jq` from any distribution and the official Bitwarden CLI. Every
backup holds this file as `RESTORE.md`, the repository has it as
`docs/restore.md`.

## What a backup holds

    personal-export.zip       data.json (bw import takes it) and attachments folders not to use
    org-<orgId>-export.json   one per organization
    item-attachments/<itemId>/<fileName>   every attachment by the id of its item
    raw/items.json            every active item as bwtidy saw it
    raw/trash.json            the trash, which no export holds
    raw/folders.json, raw/collections.json, raw/organizations.json
    RESTORE.md                this file

The `attachments` folders in `personal-export.zip` are filed by item name and
lose files of items with the same name. Take attachments from
`item-attachments` only. A second attachment with the file name of another on
the same item is stored as `<attachmentId>-<fileName>`.

## 1. Decrypt the backup on a tmpfs

The decrypted files hold every secret in plain text, so they stay in RAM
(`/dev/shm`) and are deleted at the end (step 7). Pick the newest backup from
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
also in any backup that opens
(`jq '.[] | select(.name == "bwtidy") | .fields' raw/items.json`).

```sh
D=$(mktemp -d -p /dev/shm bwtidy-restore.XXXXXX)
cd "$D"
cp /path/to/FILE.zip.age .
age -d -o backup.zip FILE.zip.age && rm FILE.zip.age
unzip -q backup.zip && rm backup.zip
unzip -q personal-export.zip data.json
jq length raw/items.json raw/trash.json
jq -r '.[] | "\(.id) \(.name)"' raw/organizations.json
```

## 2. The account to import into

Import into an empty account: a new one, or the old one after Settings, My
account, Purge vault. An import into a vault with items adds the backup's
items next to them, it does not replace anything.

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
bw list organizations | jq -r '.[] | "\(.id) \(.name)"'
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

First write the program that pairs the items:

```sh
cat > pair.jq <<'EOF'
def names: map(select(.id) | {key: .id, value: .name}) | from_entries;
def distinct($where):
  if (map(.name) | unique | length) == length then .
  else error("\($where) has organizations of the same name, give each a name of its own")
  end;
def place($n): [.type, .name, (.organizationId | if . then $n.o[.] // "id \(.)" else null end)];
def key($n):
  place($n) + [
    (.folderId | if . then $n.f[.] else null end),
    ([.collectionIds[]? | $n.c[.]] | sort),
    (del(.id, .revisionDate, .creationDate, .deletedDate, .organizationId, .collectionIds,
         .folderId, .attachments)
     | [paths(type != "object" and type != "array") as $p | [$p, getpath($p)]
        | select(.[1] != null)] | sort)]
  | tojson;
def members:
  reduce (.attachments // [])[] as $a ({used: {}, out: []};
    .used as $u
    | ($a.fileName | until($u[.] | not; "\($a.id)-\(.)")) as $m
    | .used[$m] = true
    | .out += [{id: $a.id, file: $a.fileName, member: $m}])
  | .out;

[inputs] as [$old, $of, $oc, $oo, $new, $nf, $nc, $no]
| {f: ($of | names), c: ($oc | names), o: ($oo | distinct("the backup") | names)} as $on
| {f: ($nf | names), c: ($nc | names), o: ($no | distinct("the account") | names)} as $nn
| ($new | map({key: .id, value: .}) | from_entries) as $byid
| ($new | map({id, place: place($nn)})) as $places
# the same content: the first unused imported item with the same key
| reduce $old[] as $i ({pool: (reduce $new[] as $n ({}; .[$n | key($nn)] += [$n.id])),
                         used: {}, pairs: {}};
    ($i | key($on)) as $k
    | if (.pool[$k] // []) == [] then .
      else .pairs[$i.id] = .pool[$k][0] | .used[.pool[$k][0]] = true | .pool[$k] |= .[1:]
      end)
# else the one unused imported item of its type, name and organization
| reduce $old[] as $i (.;
    . as $s
    | ($i | place($on)) as $p
    | [$places[] | select(($s.used[.id] | not) and .place == $p) | .id] as $ids
    | if $s.pairs[$i.id] or ($ids | length) != 1 then .
      else .pairs[$i.id] = $ids[0] | .used[$ids[0]] = true
      end)
| .pairs as $pairs
| $old[]
| select(.attachments)
| .id as $old_id
| ($pairs[$old_id] // "-") as $new_id
| reduce members[] as $m ({left: [$byid[$new_id].attachments[]?.fileName], out: []};
    (.left | index($m.file)) as $x
    | [$old_id, $m.id, $m.file, $m.member, $new_id] as $line
    | if $new_id == "-" then .out += [["lost"] + $line]
      elif $x != null then .left |= del(.[$x]) | .out += [["there"] + $line]
      else .out += [["new"] + $line]
      end)
| .out[]
| @sh "upload \(.)"
EOF
```

It writes one line per attachment,
`upload STATE OLDITEM ATTACHMENT FILENAME MEMBER NEWITEM`, with the state
`new` (to upload), `there` (the new item has an attachment of that file name
already) or `lost` (no imported item fits). This is what runs such a line. bw
names an attachment after its file, so each one is copied to its real file
name first:

```sh
upload() {
  case "$1" in
    there) return ;;
    lost) echo "no imported item fits backup item $2, attachment $3 is not uploaded"; return ;;
  esac
  if [ ! -f "item-attachments/$2/$5" ]; then
    echo "attachment $3 of backup item $2 has no file in the backup, it is not uploaded"
    return
  fi
  mkdir -p "upload/$3" && cp "item-attachments/$2/$5" "upload/$3/$4" &&
    bw create attachment --file "upload/$3/$4" --itemid "$6" > /dev/null &&
    echo "attachment $3 of backup item $2 is on item $6"
}
OLD=raw/items.json
```

Then list what the account has now, pair it with the backup items of `$OLD`
into `upload.sh` and run that:

```sh
bw sync
for o in items folders collections organizations; do bw list "$o" > "new-$o.json"; done
jq -n -r -f pair.jq "$OLD" raw/folders.json raw/collections.json raw/organizations.json \
  new-items.json new-folders.json new-collections.json new-organizations.json > upload.sh &&
  . ./upload.sh
rm -rf upload
```

A failed upload prints bw's error. Run the last block again: the attachments
already there are skipped, so it only adds what is missing.

`jq` stops at once with "has organizations of the same name" when two
organizations share a name in the backup or the account (`raw/organizations.json`
or `new-organizations.json`): give each a name of its own in the account,
and for the backup put the new name into `raw/organizations.json` for that
id too. Then run the last block again.

Each "no imported item fits" line is a backup item whose content no imported
item has and whose type, name and organization do not tell one. When every
item of an organization is printed so, check that the organization has the
name it had in the backup (step 2). Otherwise upload by hand: the old item is
`jq '.[] | select(.id == "OLDID")' "$OLD"`, its files are under
`item-attachments/OLDID/` (the attachment ids and file names are in the old
item), `bw list items --search NAME` lists the new candidates (also those
whose name only contains NAME), compare their values, and
`bw create attachment --file FILE --itemid NEWID` with the file copied to its
real name first.

Each "has no file in the backup" line means the zip is damaged or was
unpacked only in part: decrypt and unpack it again (step 1) and run the last
block of this step again.

## 6. The trash, only if you want it

No export holds the trash, only `raw/trash.json`. bw import refuses or would
take over some of its keys, so drop them first and import the rest into the
personal vault:

```sh
jq '{encrypted: false, items: [.[] | del(.id, .deletedDate, .folderId, .organizationId, .collectionIds)]}' \
  raw/trash.json > trash-import.json
bw import bitwardenjson trash-import.json
jq -s '.[0] + (.[1] | map(del(.deletedDate, .folderId, .organizationId, .collectionIds)))' \
  raw/items.json raw/trash.json > with-trash.json
```

Then set `OLD=with-trash.json` and run the last block of step 5 again (in a
new shell, define `upload` of step 5 first). The attachments of the active
items are already there, so it only uploads those of the trashed items. They
come back as active personal items in no folder. Move them to the trash in
the web vault if that is where they belong.

## 7. Check and clean up

The account must have as many items and attachments as the backup. The
first number below is the account's items, which must equal the count of
`raw/items.json` that step 1 printed. The next two are the account's
attachments and the backup's, which must be equal. Each "not uploaded" line
of step 5 is one attachment missing. With the trash imported (step 6), the
item count is that of both files of step 1, and `with-trash.json` takes the
place of `raw/items.json`: set `ITEMS=with-trash.json` instead of the first
line below.

```sh
ITEMS=raw/items.json
bw sync
bw list items | jq length
bw list items | jq '[.[] | (.attachments // []) | length] | add'
jq '[.[] | (.attachments // []) | length] | add' "$ITEMS"
bw logout
cd ~ && rm -rf "$D" ${CLI:+"$CLI"}
unset BW_SESSION BITWARDENCLI_APPDATA_DIR
```

Then log bwtidy in to the restored account (Log out, with "Also log out of
Bitwarden" checked if the account changed).

## What does not come back

Checked on the dev vault by `backend/tests/integration/test_backup_import.py`
in the repository:

- Every id is new: items, attachments, folders and collections. bwtidy's
  Change log names the old ids, so its Restore buttons cannot find the
  restored items.
- `creationDate` and `revisionDate` are new.
- Organization items land in the organization you import into (the one of
  the same name, step 4), in its collections of the same names. Its members,
  groups and policies are not in the backup.
- The trash comes back only as active items, without its deletion dates.

Everything else came back as it was: every value of every item (custom
fields with duplicate names and trailing spaces included), passkeys,
password history, favorites, master password reprompt, folders and
collections by name, which item is in which folder and collections, and
every attachment with its file name and bytes.

Not in a backup at all: Sends, account settings (two-step login, emergency
access, devices) and organization settings.

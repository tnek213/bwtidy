# Secret values and backups

bwtidy treats some values as secret. Such a value stays in your vault and in
encrypted files. The worker holds it in memory while you are logged in. You
see one only when you reveal it.

```graphviz alt="Your vault loads into the worker's memory at the unlock. From there a secret reaches the grid one revealed cell at a time. An export of the vault goes into the backups encrypted with your master password. A grid edit or a new item's secret waits age-encrypted in the queue until the worker decrypts it for its write. No log or error message gets a secret, and no MCP answer or Change log row does either."
digraph {
  rankdir=TB
  nodesep=0.35
  ranksep=0.5
  fontname="sans-serif"
  node [fontname="sans-serif" fontsize=13 shape=box style="rounded,filled" fillcolor="#ffffff" color="#5c6bc0" margin="0.12,0.06"]
  edge [fontname="sans-serif" fontsize=12 color="#455a64"]
  vault [label="Your Bitwarden vault" shape=cylinder fillcolor="#e8eaf6"]
  worker [label="Worker's memory\nwhile you are logged in" fillcolor="#e8eaf6"]
  grid [label="Grid\neach revealed cell\nfor up to 30 seconds"]
  queue [label="Queue in SQLite\nage-encrypted" shape=cylinder fillcolor="#f1f8e9" color="#558b2f"]
  backups [label="Backups\nencrypted with your\nmaster password" shape=cylinder fillcolor="#f1f8e9" color="#558b2f"]
  never [label="Logs\nerror messages\nMCP answers\nChange log" fillcolor="#ffebee" color="#c62828" fontcolor="#b71c1c"]
  vault -> worker [label=" unlock"]
  worker -> grid [label=" reveal"]
  grid -> queue [label=" edit or new item"]
  queue -> vault [label=" decrypt and write"]
  vault -> backups [label=" export"]
  worker -> never [label=" never" style=dashed arrowhead=tee color="#c62828" fontcolor="#b71c1c"]
}
```

## Which values are secret

These values are secret:

- a login's password and its TOTP
- a card's number and its security code
- an SSH key's private key
- every custom field of the hidden type

Everything else that bwtidy shows is plain. That covers the name and the
notes as well as the URIs and the username. Text custom fields and attachment
file names are plain too.

> [!WARNING]
> A secret in a note or a text field is not protected, so move it to a
> hidden field.

## In the grid

Secret cells are masked. A click reveals one cell and fetches its value from
the worker on its own. The value shows until a second click hides it or its
row scrolls away. It hides by itself after 30 seconds. The browser never
caches it.

A column is secret when any item has a hidden field of that name. On another
item the same column can hold a text field. The grid shows that value plain
with a warning corner, since an agent can read it too.

No edit may copy a secret into a plain value of the same item. bwtidy refuses
a plain value that equals one of the item's secret values. It also refuses a
secret value that equals one of the item's plain values. The refusal names
the two columns but never the value.

## Where they never go

Secret values never reach a log or an error message. They never reach an MCP
answer or the Change log either. The SQLite file holds a secret only in
age-encrypted form.

An edit of a secret cell waits in the queue encrypted, and so does a new
item's secret value. The key that encrypts them is itself encrypted with
your master password. bwtidy decrypts the value only in memory. It does so to
write the value and to compare it with a new plain value of the item. A failed
edit loses its value at the next restart.

Your master password is a secret too.
[Logging in](login.md#what-the-master-password-is-used-for) says where it
goes.

## Backups

Every write of your changes has a backup of the whole vault as it is right
before the write. Only the few writes that an unlock makes to the bwtidy note
come without one (see [How bwtidy writes it](tool-note.md#how-bwtidy-writes-it)).
Each backup is one file `TIMESTAMP.zip.age`. It is a zip encrypted with
[age](https://age-encryption.org) with your master password as the
passphrase. You open it with `age -d` at a terminal.

A backup holds these parts:

- the personal vault as `bw export --format zip` makes it
- one JSON export for each organization
- every attachment of every item, also those in the trash and the archive
- the raw listings of the items and the trash as bwtidy reads them
- a raw listing of the archived items, which the other listings leave out
- the raw listings of the folders, collections and organizations
- `RESTORE.md` with the steps that bring a lost vault back

`RESTORE.md` is a copy of
[Restore a vault from a backup](../restore.md), which also shows
[every file of a backup](../restore.md#what-a-backup-holds). Its steps need
nothing of bwtidy.

The exports go from the CLI into memory and bwtidy builds the zip there. No
plain copy of your vault touches the disk.

bwtidy keeps the newest 40 backups that are at most 90 days old. It always
keeps the newest 5 whatever their age.
[Configuration](../reference/configuration.md#set-in-env) shows how to change
these numbers. After you change your master password, the next login
re-encrypts every backup with the new one. A backup that opens with none of
your earlier passwords stays as it is and bwtidy names it after the login.

## For developers

One function decides what is secret and every check of the secret rules asks
it. The architecture names the code behind this page in
[Where the secret rules are enforced](../architecture.md#where-the-secret-rules-are-enforced).

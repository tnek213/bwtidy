# Secret values and backups

bwtidy treats some values as secret. They stay in your vault, in the
worker's memory while you are logged in and in encrypted files, and you see
one only when you reveal its cell.

## Which values are secret

A login's password and TOTP, a card's number and security code, an SSH key's
private key, and every custom field of the hidden type. Everything else is
plain: the name, the notes, the URIs, the username, text custom fields and
attachment file names.

> [!WARNING]
> A secret in a note or a text field is not protected, so move it to a
> hidden field.

## In the grid

Secret cells are masked. A click reveals one cell: its value is fetched
from the worker on its own, shown until its row scrolls away or 30 seconds
pass, and not kept by the browser. A text field in a secret column is shown
plain with a warning corner, as an agent can read it too.

No edit may copy a secret into a plain value of the same item: a plain value
equal to one of the item's secret values is refused, and so is a secret
value equal to one of its plain values. The refusal names the two columns,
never the value.

## Where they never go

Secret values never reach a log, an error message, an MCP answer or the
change log. In the SQLite file a secret is only age-encrypted: an edit of a
secret cell, or a new item's secret value, waits in the queue encrypted with
a key that is itself encrypted with your master password. It is decrypted
only for the write. A failed edit loses its value at the next restart.

Your master password is a secret too. Where it goes is in
[Logging in](login.md#what-the-master-password-is-used-for).

## Backups

Every write has a backup of the whole vault from right before it: one file
`TIMESTAMP.zip.age` per backup, a zip encrypted with
[age](https://age-encryption.org), and your master password is the
passphrase. `age -d` opens it at a terminal. It holds:

- the personal vault as `bw export --format zip` makes it, and one JSON
  export per organization. `bw import` has no zip format, so a restore
  imports the `data.json` from the zip (see [Restore a vault from a
  backup](../restore.md)),
- every attachment of every item, active and in the trash,
- the raw listings of the items, the trash, folders, collections and
  organizations, as bwtidy reads them,
- `RESTORE.md`, the steps to bring a lost vault back with only age, Python 3,
  bash and the Bitwarden CLI, on Linux, macOS or Windows (the same as [Restore a vault from a
  backup](../restore.md)).

The exports go from the CLI into memory and the zip is built there, so no
plain copy of your vault touches the disk. bwtidy keeps the newest 40
backups that are at most 90 days old, and always the newest 5 (see
[Configuration](../reference/configuration.md)). After you change your master
password, the next login re-encrypts every backup with the new one.

## For developers

One function decides what is secret and every place that shows, stores or
logs a value asks it. The code behind this page: [docs/architecture.md,
section Where the secret rules are
enforced](../architecture.md#where-the-secret-rules-are-enforced).

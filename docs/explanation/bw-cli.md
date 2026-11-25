# How bwtidy uses the Bitwarden CLI

bwtidy never talks to your Bitwarden server itself. Every read and every
write goes through the official Bitwarden CLI (`@bitwarden/cli`), which the
image carries at version 2026.10.0. So your vault is decrypted and encrypted
by Bitwarden's own code, and the server sees a CLI client like any other.

## Which version of the CLI

bwtidy uses the CLI exactly as Bitwarden publishes it on npm, and aims to
ship its latest version. A new release of the CLI is followed by a bwtidy
patch release, usually within a few weeks, sometimes later. The security of
the CLI and of the packages it brings along is Bitwarden's, so report an
issue in them to Bitwarden.

## `bw serve` for reads and writes

Each account's worker starts its own `bw serve`, the CLI's local REST API.
It listens on a unix socket in the account's directory, never on a network
port, because it has no login of its own: whoever reaches it can use the
unlocked vault.

Through it bwtidy unlocks and locks the vault, syncs, lists the items,
folders, collections and organizations, and writes: it edits an item by
fetching the whole item and putting it back with only the changed values,
so fields it does not know (passkeys, password history) survive. It also
creates items, moves them to the trash or out of it, creates, renames and
deletes folders, moves items into an organization, sets their collections,
uploads, downloads and deletes attachments, and asks the generator for new
passwords.

If `bw serve` exits, the worker starts it again and unlocks it with the
password it holds, so your session goes on.

## The CLI for the rest

Some things run as one `bw` command each:

- The login: `bw config server` (when the server is another one) and
  `bw login`, see [Logging in](login.md). `bw status` tells whether the CLI
  is logged in, and `bw logout` logs it out when you ask for it.
- The backups: `bw export` of the personal vault and of each organization,
  `bw list items --trash` and `bw get attachment` for every attachment. They
  get the session key in their environment and write to bwtidy's memory,
  never to a file. See [Secret values and backups](secrets.md).

All of them share one CLI state, the account's `bw` directory, so bwtidy
runs them one at a time and stops a `bw` gently (SIGTERM, and SIGKILL only 5
seconds later), since a killed `bw` can leave that state empty. A `bw unlock`,
`bw lock` or `bw logout` you run in the container yourself uses that state
too, and invalidates the session key the backups get, so
[Run bwtidy](../how-to/run.md#log-in-the-first-time) asks you to leave them
alone while you are logged in.

## For developers

Reads and writes go through one long-running `bw serve` because each
separate `bw` command costs about a second of startup. The code behind this
page: [docs/architecture.md, section The whole on one
page](../architecture.md#the-whole-on-one-page), layer 2.

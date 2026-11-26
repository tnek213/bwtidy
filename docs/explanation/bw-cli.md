# How bwtidy uses the Bitwarden CLI

bwtidy never talks to your Bitwarden server itself. Every read and every
write of your vault goes through the official Bitwarden CLI
(`@bitwarden/cli`) at version 2026.10.0, which the image carries. Bitwarden's
own code decrypts and encrypts your vault. The server sees a CLI client like
any other.

```mermaid alt="The account's worker reaches the Bitwarden CLI in two ways. A long-running bw serve takes the reads and writes, and single bw commands run the login and the backups. Both share the CLI state in the account's bw directory and send requests to your Bitwarden server."
flowchart LR
  Worker["Account's worker"]
  subgraph CLI["Bitwarden CLI"]
    direction TB
    Serve["bw serve on a unix socket<br/>reads and writes"]
    Commands["Single bw commands<br/>login, logout and backups"]
  end
  State[("CLI state in the<br/>account's bw directory")]
  Server["Your Bitwarden server"]
  Worker --> Serve
  Worker --> Commands
  CLI --- State
  CLI --> Server
```

## Which version of the CLI

bwtidy uses the CLI exactly as Bitwarden publishes it on npm and aims to ship
its latest version. A new release of the CLI is followed by a bwtidy patch
release, usually within a few weeks and sometimes later. Bitwarden is
responsible for the security of the CLI and the packages it brings along, so
report an issue in them to Bitwarden.

## `bw serve` for reads and writes

Each account's worker starts its own `bw serve`, which is the CLI's local REST
API. It listens on a unix socket in the account's directory and never on a
network port. `bw serve` has no login of its own, so whoever reaches it can
use the unlocked vault.

Through `bw serve` bwtidy does all of this:

- unlocks and locks the vault and syncs it
- lists the vault's items and folders as well as its collections and
  organizations
- edits items
- creates items and moves them to the trash or out of it
- creates, renames and deletes folders
- moves items into an organization and sets their collections
- uploads, downloads and deletes attachments
- asks the generator for new passwords

An edit fetches the whole item and puts it back with only the changed values.
That way the fields bwtidy does not know survive, such as passkeys and the
password history.

If `bw serve` exits, the worker starts it again and unlocks it with the
password it holds. Your session goes on unless that unlock fails.

## The CLI for the rest

The login and the backups run the CLI as single `bw` commands. The login runs
`bw config server` when the server differs from the one the CLI knows and
then `bw login`, as [Logging in](login.md) explains. `bw logout` logs the CLI
out when you ask for it. When `bw serve` cannot tell whether the CLI is logged
in, `bw status` answers instead.

A backup first syncs and lists the vault through `bw serve`. Then it runs
`bw export` for the personal vault and for each organization. Next come
`bw list items --trash` and one `bw get attachment` for every attachment.
These commands get the session key in their environment and write to
bwtidy's memory instead of a file. [Secret values and backups](secrets.md)
tells you more.

`bw serve` and every `bw` command share one CLI state in the account's `bw`
directory. So bwtidy runs the single commands one at a time. The backup's
commands run while `bw serve` stays up. The login and the logout stop
`bw serve` first and start it again afterwards. bwtidy stops a `bw` gently
with SIGTERM and sends SIGKILL only when the process is still there 5 seconds
later. A killed `bw` can leave the state empty, which logs the CLI out.

A `bw unlock`, `bw lock` or `bw logout` that you run in the container yourself
uses the same state. It also invalidates the session key that the backups
get. So [Run bwtidy](../how-to/run.md#log-in-the-first-time) asks you to
leave these commands alone while you are logged in.

## For developers

bwtidy prefers one long-running `bw serve` to separate `bw` commands because
each command costs about a second of startup. The code behind this page is
the layer `bw/` in [The whole on one page](../architecture.md#the-whole-on-one-page)
of docs/architecture.md.

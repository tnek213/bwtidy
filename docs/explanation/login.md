# Logging in

Getting into your vault takes up to three steps. You pick the account, log
the Bitwarden CLI in once and unlock the vault. The steps to follow are in
[Run bwtidy](../how-to/run.md#log-in-the-first-time), and this page explains
what happens behind them.

Whether the Bitwarden login or the unlock comes next depends on whether the
account's CLI is logged in. The CLI stays logged in from one session to the
next, so most of the time you only unlock.

```mermaid alt="A browser without a remembered account starts at the account form. The account form and a remembered account both lead to the Bitwarden login while the CLI is logged out and to the unlock while it is logged in. Both start a session, and a failed unlock at the Bitwarden login leaves the unlock to try again. When the session ends the same choice comes again, and Switch account goes back to the account form."
stateDiagram-v2
  direction LR
  state "Account form" as Account
  state "Bitwarden login" as BwLogin
  state "Unlock" as Unlock
  state "Session" as Session
  state cli <<choice>>
  [*] --> Account: no account remembered
  [*] --> cli: account remembered
  Account --> cli
  cli --> BwLogin: CLI logged out
  cli --> Unlock: CLI logged in
  BwLogin --> Session
  BwLogin --> Unlock: unlock fails
  Unlock --> Session
  Session --> cli: session ends
  Session --> Account: Switch account
```

## The account

An account is a Bitwarden server and an email. The account form only picks
the account. It starts nothing and asks for no password. The browser
remembers the account, so the next time you skip the form. A new account
gets its own directory at its first Bitwarden login and keeps it only when
that login and the first unlock succeed. bwtidy refuses a server that does
not resolve to a public address unless `BWTIDY_ALLOWED_SERVERS` allows it (see
[Configuration](../reference/configuration.md#set-in-env)).

## The Bitwarden CLI login

bwtidy runs the real `bw login` for you in a pseudo-terminal and shows its
output in the console under the form. The master password reaches the CLI
through its environment and never through its command line. Before the
console shows or keeps a line it replaces the password and the session key
with bullets.

The CLI may ask questions, which you answer in the field under the console.
On the first login from a new device bitwarden.com emails you a one-time
code. An account with two-step login asks for its code. Once the login is
done the CLI keeps it in the account's directory and from then on you only
unlock.

## The unlock

The unlock sends the master password to the account's `bw serve`, which
decrypts the vault. Then bwtidy syncs and loads the vault into the worker's
memory. It makes sure [its note](tool-note.md) is there and re-encrypts the
backups if your master password changed. The Bitwarden login unlocks with
the password you typed for it, so you type it only once.

## The session

A session is a cookie in this browser. Only one runs per account. A new
login replaces it but needs the running session's master password. After 3
wrong passwords in a row bwtidy refuses every try for 1 second, even one with
the right password. Each further wrong password doubles that wait up to a
minute.

The session ends when one of these happens:

- you log out or switch account
- 2 minutes pass after the last tab closed
- 2 hours pass without activity
- the container stops

Both times are defaults that
[Configuration](../reference/configuration.md#set-in-env) lets you change.
At the end your unsaved edits are written first. Then `bw serve` is locked
and the password and the session key are wiped from memory. MCP is switched
off and the worker exits. The CLI stays logged in unless you tick "Also log
out of Bitwarden" in the Log out dialog.

## What the master password is used for

bwtidy needs your master password for these things:

- It unlocks `bw serve` and logs the CLI in.
- It is the passphrase of every backup and of the key that encrypts your
  queued secret edits (see [Secret values and backups](secrets.md)).
- A slow hash of it stays in memory to check a new login against the running
  session.
- A copy sits in a hidden field of the bwtidy note. After you change your
  master password the next login can still open the old backups and
  re-encrypt them (see [The bwtidy note](tool-note.md)).

The password stays in the worker's memory while the session runs. Outside
your vault it never goes to disk. It never reaches a log or the SQLite file.
The browser's storage and agents never get it either.

## For developers

The frontend decides whether you see a login page or the app. The gateway
starts a worker only for a login or an unlock. Before any worker runs the
gateway reads the CLI's last known login state from a file in the account's
directory, which each worker updates. The worker's session
manager holds the master password and the session key until the session
ends. The code behind this page is in
[Backend packages](../architecture.md#backend-packages) of
docs/architecture.md.

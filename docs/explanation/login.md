# Logging in

Getting into your vault takes up to three steps: pick the account, log the
Bitwarden CLI in (once), and unlock. The steps to follow are in
[Run bwtidy](../how-to/run.md#log-in-the-first-time). This page says what
happens behind them.

## The account

An account is a Bitwarden server and an email. The account form only picks
it: it starts nothing and asks for no password. The browser remembers it, so
next time you go straight to the unlock. A new account gets its own
directory at its first Bitwarden login, and keeps it only when that login
succeeds. bwtidy refuses a server that resolves to a private address unless
the setup allows it (`BWTIDY_ALLOWED_SERVERS` in
[Configuration](../reference/configuration.md#set-in-env)).

## The Bitwarden CLI login

bwtidy runs the real `bw login` for you, in a pseudo-terminal, and shows its
output in the console under the form. The master password reaches the CLI
through its environment, never its command line. The console replaces the
password and the session key with bullets before it shows or keeps a line.

The CLI may ask questions, which you answer in the field under the console:
bitwarden.com emails a one-time code on the first login from a new device,
and an account with two-step login asks for its code. When it is done, the
CLI's login is saved in the account's directory and the next time you only
unlock.

## The unlock

The unlock sends the master password to the account's `bw serve`, which
decrypts the vault. bwtidy then syncs, loads the vault into the worker's
memory, makes sure [its note](tool-note.md) is there, and re-encrypts the
backups if your master password changed. The same password typed at the
Bitwarden login also unlocks, so you type it once.

## The session

A session is a cookie in this browser. Only one runs per account: a new
login replaces it, and needs the running session's master password. After 3
wrong ones each try waits longer.

The session ends when you log out or switch account, 2 minutes after the
last tab closed, after 2 hours without activity (both by default), and when
the container stops. Your unsaved edits are written first. Then `bw serve`
is locked, the password and the session key are wiped from memory, MCP is
switched off, and the worker exits. The CLI stays logged in unless you tick
"Also log out of Bitwarden" in the Log out dialog.

## What the master password is used for

- Unlocking `bw serve`, and the CLI login.
- The passphrase of every backup, and of the key that encrypts your queued
  secret edits (see [Secret values and backups](secrets.md)).
- A slow hash of it, kept in memory, to check a new login against the
  running session.
- A copy in a hidden field of the bwtidy note, so that after you change your
  master password the next login can still open and re-encrypt the old
  backups (see [The bwtidy note](tool-note.md)).

It stays in the worker's memory while the session runs. It never goes to
disk (other than inside your vault), to a log, to the SQLite file, to the
browser's storage or to an agent.

## For developers

The frontend decides what you see (account form, Bitwarden login, unlock or
the app), the gateway starts a worker only for a login or an unlock, and the
worker's session manager holds the master password and the key until the
session ends. The code behind this page: [docs/architecture.md, section
Backend packages](../architecture.md#backend-packages).

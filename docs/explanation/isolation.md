# How accounts are kept apart

Several Bitwarden accounts can use one bwtidy, each kept apart from the
others by plain Linux file permissions: every account gets a Linux user id
(uid) of its own, and its worker runs as that uid.

## One uid per account

- The gateway runs as root, since only root can start a process as another
  user. The container keeps only the six capabilities it needs to make and
  own the account directories, switch to their uids and stop the workers.
- A new account gets one more than the highest uid in use, from 70001 up
  (`BWTIDY_UID_BASE` plus one). A backup directory that outlived its
  account keeps its uid in use. The uids have no user names.
- The gateway starts the account's worker as that uid, with no other groups,
  a umask of 077 and an environment built from scratch. The worker's own
  `bw serve` runs as the same uid.

## What each uid owns

The account's directory, `/data/accounts/HOST/EMAIL`, is owned by its uid
and closed to every other uid (mode 0700). It holds the SQLite file, the hash
of the MCP token, the Bitwarden CLI's login state, the sockets of the worker
and of `bw serve`, and the worker's log. The account's backup directory,
`/backups/HOST/EMAIL`, is the same. The directories above them let a worker
pass through to its own directory but not list the others (mode 0711).

## What that keeps apart

- A worker cannot read another account's database, CLI login, backups or
  log.
- A worker cannot reach another account's unlocked vault: `bw serve` has no
  login of its own, so it listens only on a socket inside the account's
  directory, and that is closed to other uids.
- The browser's cookie names an account, and a worker cannot change it. An
  MCP token leads only to the account whose directory holds its hash.

## What root can still see

- The gateway, as root, can read every account's directory. It reads three
  small files there (the MCP token's hash, whether the CLI is logged in, and
  the marker of an account that asked to be deleted), each only when it is a
  regular file the account's uid owns, never through a symlink.
- Anyone who is root on the host, or can run Docker there, can read every
  file and every worker's memory, the unlocked vault included. bwtidy does
  not protect against them. The web port is published only on the host's
  127.0.0.1.
- The SQLite file is not encrypted. A secret value is in it only
  age-encrypted, but the queue's diffs hold names, notes and URIs in plain
  text. Its directory's mode is what keeps other uids out.

## For developers

The gateway makes each account's directory and starts its worker under its
own uid, and the capabilities and the read-only root file system come from
the compose file. The code behind this page: [docs/architecture.md, section
The container](../architecture.md#the-container).

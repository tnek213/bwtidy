# How accounts are kept apart

Several Bitwarden accounts can share one bwtidy. Plain Linux file
permissions keep each account apart from the others. Every account gets a
Linux user id (uid) of its own, and its worker runs as that uid.

```graphviz alt="The gateway runs as root and starts each account's worker as that account's own uid. A worker and its bw serve use their own account's directory and backups but cannot open those of another account."
digraph {
  nodesep=0.5
  ranksep=0.4
  fontname="sans-serif"
  node [fontname="sans-serif" fontsize=13 shape=box style="rounded,filled" fillcolor="#ffffff" color="#5c6bc0" margin="0.12,0.06"]
  edge [fontname="sans-serif" fontsize=12 color="#455a64"]
  gateway [label="Gateway as root" fillcolor="#e8eaf6"]
  subgraph cluster_a {
    label="Account A as uid 70001" labelloc=b style="rounded,filled" fillcolor="#f1f8e9" color="#558b2f" fontcolor="#33691e"
    worker_a [label="Worker and its bw serve"]
    files_a [label="Account directory\nand backups, mode 0700" shape=cylinder]
    worker_a -> files_a
  }
  subgraph cluster_b {
    label="Account B as uid 70002" labelloc=b style="rounded,filled" fillcolor="#f1f8e9" color="#558b2f" fontcolor="#33691e"
    worker_b [label="Worker and its bw serve"]
    files_b [label="Account directory\nand backups, mode 0700" shape=cylinder]
    worker_b -> files_b
  }
  gateway -> worker_a [label=" starts as uid 70001"]
  gateway -> worker_b [label=" starts as uid 70002"]
  worker_a -> files_b [label="no access" style=dashed arrowhead=tee color="#c62828" fontcolor="#b71c1c"]
  worker_b -> files_a [label="no access" style=dashed arrowhead=tee color="#c62828" fontcolor="#b71c1c"]
}
```

## One uid per account

The gateway runs as root so that it can start each worker as another user.
The container keeps only the six capabilities the gateway needs. They let it
make the account directories and give them to their uids. They also let it
start the workers as those uids and stop them.

A new account gets one more than the highest uid in use. The lowest is
`BWTIDY_UID_BASE` plus one, which is 70001 by default. A backup directory
that outlived its account keeps its uid in use, so no new account gets that
uid. The uids have no user names.

The gateway starts the account's worker as that uid with no other groups.
The worker gets a umask of 077 and an environment built from scratch. Its own
`bw serve` runs as the same uid.

## What each uid owns

The account's directory is `/data/accounts/HOST/EMAIL`. Its uid owns it and
mode 0700 shuts out the other accounts' uids. It holds the SQLite file and
the hash of the MCP token. The Bitwarden CLI's login state is there too, as
well as the sockets of the worker and of `bw serve` and the worker's log.
The account's backups in `/backups/HOST/EMAIL` are kept the same way.

The directories above them up to `/data/accounts` and `/backups` have mode
0711. A worker can pass through them to its own directory but cannot list
the other accounts. The whole layout is
in [Configuration](../reference/configuration.md#files-and-volumes).

## What that keeps apart

- A worker cannot read another account's files. That covers its database and
  CLI login as well as its backups and log.
- A worker cannot reach another account's unlocked vault. `bw serve` has no
  authentication of its own, so it listens only on a socket inside the
  account's directory. The other accounts' uids cannot open that directory.
- The browser's cookie leads to one account, and a worker cannot change it.
- An MCP token leads only to the account whose directory holds its hash.

## What root can still see

- The gateway runs as root and can read every account's directory. It reads
  only three small files there. They are the MCP token's hash, whether the
  CLI is logged in and the marker of an account that asked to be deleted.
  The gateway trusts a file only when the file itself is a regular file and
  not a symlink, and the account's uid owns it.
- Anyone who is root on the host or can run Docker there can read every file
  and every worker's memory, the unlocked vault included. bwtidy does not
  protect against that. The web port is published only on the host's
  127.0.0.1.
- The SQLite file is not encrypted. A secret value is in it only
  age-encrypted, but the queue holds other vault values in plain text. Only
  the mode of its directory keeps the other accounts' uids out. What the file
  holds is in [Configuration](../reference/configuration.md#files-and-volumes).

## For developers

The gateway makes each account's directory and starts its worker under its
own uid. The capabilities and the read-only root file system come from the
compose file. The code behind this page is in
[docs/architecture.md, section The container](../architecture.md#the-container).

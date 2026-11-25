# How bwtidy fits together

bwtidy is one Docker container. Inside it run a small web server (the
gateway), one worker for each Bitwarden account in use, and the official
Bitwarden CLI. Your browser and your AI agent only ever talk to the gateway,
and only the Bitwarden CLI talks to your Bitwarden server.

```
browser ------+                 +--> worker A (uid A) ----> bw serve A --+
              | http(s)         |                                        +--> Bitwarden
MCP client ---+--> gateway -----+                                        |    server
 (token)           root         +--> worker B (uid B) ----> bw serve B --+
```

## The parts

- The gateway serves the web page, the account form and these docs (at
  `/docs/`), and passes every other request on to the worker of your
  account. It never logs or stores
  what passes through it. An MCP client finds its account by its token.
- A worker is bwtidy itself for one account: the grid's data, the queue, the
  backups and the MCP tools. The gateway starts it when you click Unlock or
  Log in, and it exits when your session ends, taking the vault out of
  memory with it.
- Each worker runs its own `bw serve`, the Bitwarden CLI's local API, which
  holds the unlocked vault and does every read and write. See
  [How bwtidy uses the Bitwarden CLI](bw-cli.md).
- Each account's worker runs as a Linux user of its own, so accounts cannot
  read each other's data. See [How accounts are kept apart](isolation.md).

## Where your data is

- Your vault stays on your Bitwarden server. While you are logged in, the
  worker keeps a copy of it in memory.
- On disk each account has a directory with the Bitwarden CLI's login, a
  SQLite file (the queue, the change log and the settings) and a log. The
  backups are encrypted files in a directory of their own. See
  [Configuration](../reference/configuration.md#files-and-volumes).
- In the vault itself bwtidy keeps one secure note of its own. See
  [The bwtidy note](tool-note.md).
- bwtidy has no telemetry. It sends nothing to its author or anyone else,
  and the only connection out of the container is the Bitwarden CLI's to
  your Bitwarden server.

## What happens when you use it

1. You pick the account and log in: see [Logging in](login.md).
2. You edit in the grid, or an agent proposes changes over MCP. Both wait in
   the queue: see [How edits are saved](edits.md) and
   [What an agent can do](mcp.md).
3. Before each write bwtidy takes an encrypted backup, and every write lands
   in the change log, from where you can undo it. Secret values stay masked
   throughout: see [Secret values and backups](secrets.md).

## For developers

The gateway and the worker are two entry points of one Python package: the
gateway proxies `/api/*` and `/mcp` to the worker of the account over a unix
socket, and serves the built frontend. The code behind this page:
[docs/architecture.md, section The whole on one
page](../architecture.md#the-whole-on-one-page).

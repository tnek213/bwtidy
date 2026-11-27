# How bwtidy fits together

bwtidy is one Docker container. Inside it run a small web server called the
gateway, one worker for each Bitwarden account in use and the official
Bitwarden CLI. Your browser and your AI agent only ever talk to the gateway.
Only the Bitwarden CLI talks to your Bitwarden server.

```mermaid alt="The browser and an MCP client with its token reach the gateway inside the bwtidy container over HTTP. The gateway runs as root and passes each request on to the worker of its account. Each worker runs as its account's own uid with a Bitwarden CLI of its own, and only the Bitwarden CLI talks to the Bitwarden server outside the container."
%%{init: {"htmlLabels": false, "flowchart": {"htmlLabels": false, "rankSpacing": 32, "nodeSpacing": 24}}}%%
flowchart LR
  browser["Browser"]
  agent["MCP client<br/>with its token"]
  subgraph container["bwtidy container"]
    gateway["Gateway<br/>as root"]
    subgraph a["Account A"]
      workerA["Worker A<br/>as uid A"] --> serveA["Bitwarden CLI A<br/>bw serve and bw commands"]
    end
    subgraph b["Account B"]
      workerB["Worker B<br/>as uid B"] --> serveB["Bitwarden CLI B<br/>bw serve and bw commands"]
    end
    gateway --> workerA
    gateway --> workerB
  end
  server[("Bitwarden<br/>server")]
  browser -- HTTP --> gateway
  agent -- HTTP --> gateway
  serveA --> server
  serveB --> server
```

## The parts

- The gateway serves the web page and the account form as well as these
  docs at `/docs/`. It answers a few requests itself and passes the API and
  MCP requests on to the worker of your account. It never logs or stores the
  content that passes through it unless you turn on the
  [usage stats](../reference/configuration.md#usage-stats), which keep counts
  and never your data. An MCP client finds its account by its token.
- A worker is bwtidy itself for one account, with the grid's data and the
  queue as well as the backups and the MCP tools. The gateway starts it when
  you click Unlock or Log in. It exits when your session ends and takes the
  vault out of memory with it.
- Each worker runs its own `bw serve`, the Bitwarden CLI's local API.
  `bw serve` holds the unlocked vault and does the reads and writes of your
  edits. The login and the backups run single `bw` commands. See
  [How bwtidy uses the Bitwarden CLI](bw-cli.md).
- Each account's worker runs as a Linux user of its own, so accounts cannot
  read each other's data. See [How accounts are kept apart](isolation.md).

## Where your data is

- Your vault stays on your Bitwarden server. While you are logged in the
  worker keeps a copy of it in memory.
- On disk each account has a directory with the Bitwarden CLI's login, a
  SQLite file and a log. The SQLite file holds the queue, the Change log and
  the settings. The backups are encrypted files in a directory of their own.
  See [Configuration](../reference/configuration.md#files-and-volumes).
- In the vault itself bwtidy keeps one secure note of its own. See
  [The bwtidy note](tool-note.md).
- bwtidy has no telemetry. It sends nothing to its author or anyone else.
  The only connection out of the container is the one from the Bitwarden CLI
  to your Bitwarden server. The usage stats you can turn on stay in a file on
  your machine.

## What happens when you use it

You start by picking the account and logging in, as
[Logging in](login.md) explains. Then you edit in the grid or an agent
proposes changes over MCP. Both kinds of edit wait in the queue. Yours are
written in batches, while an agent's wait for your approval (see
[How edits are saved](edits.md) and [What an agent can do](mcp.md)).

Each write has an encrypted backup of the vault from just before it. Every
write from the queue lands in the Change log, where you can undo it. Secret
values stay masked unless you reveal one (see
[Secret values and backups](secrets.md)).

## For developers

The gateway and the worker are two entry points of one Python package. The
gateway serves the built frontend and proxies `/api/*` and `/mcp` to the
worker of the account over a unix socket. The code behind this page is in
[docs/architecture.md, section The whole on one
page](../architecture.md#the-whole-on-one-page).

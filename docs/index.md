# bwtidy documentation

## Bring order to a messy vault

<!-- intro: the same text in README.md and docs/index.md -->

Password vaults get inconsistent over time. Some items use `Password` while
others use `password`. Some web addresses include a protocol prefix and others
do not. Bitwarden's apps show one item at a time. That makes these patterns
hard to spot. **bwtidy** shows the whole vault in one grid.

Templates and datatypes help you standardize items of the same kind. A
template defines which fields an item should have. A datatype describes the
expected format of a value. Together they help you find inconsistencies and
tidy the vault.

Use the web UI to make changes directly. You can also connect an AI agent
through MCP. The agent can add proposed changes to a queue without seeing
secret values. You review each proposal and decide whether to accept it. Only
accepted proposals are written to your vault.

Before each write of your changes bwtidy saves a backup of the whole vault.
The backup is an age-encrypted ZIP stored in bwtidy's backups volume. You can restore it with
standard tools. Your vault stays on your Bitwarden or Vaultwarden server.
bwtidy reads and writes vault data through Bitwarden's official CLI.

bwtidy is for people who want their Bitwarden or Vaultwarden vault to follow
standards they define themselves. It helps them apply those standards
consistently across the vault.

<!-- end of intro -->

## Beta software

> [!WARNING]
> **Made with AI.** AI coding agents wrote all of bwtidy under the author's
> guidance. Claude Fable, Opus and Sonnet built it in many small steps. Codex
> (GPT-6.2-Astra) reviewed it for security and stability. No human has read
> every line. The author uses it for real passwords but the project has few
> users so far. Because bwtidy changes your vault keep an independent Bitwarden
> export made outside bwtidy. Expect bugs and breaking changes between
> versions.

New to bwtidy? [Run it](how-to/run.md) first and then follow
[the first tutorial](tutorial/first-cleanup.md).

## At a glance

- **Your vault stays on your server.** bwtidy runs in one container on your
  machine. It reads and writes your vault through `bw`, Bitwarden's official
  CLI.
- **A backup before your changes are written.** bwtidy saves a backup of the
  whole vault before each write of your changes. The backup is a ZIP encrypted with age using your master
  password as the passphrase. It lives in bwtidy's backups volume and can be
  restored with standard tools without bwtidy.
- **Your master password.** It remains in memory while you are logged in and is
  wiped when you log out. It is also wiped two minutes after you close the last
  tab or after two hours without activity. Both timings are defaults. A copy is
  stored in the bwtidy note in your vault so older backups remain readable after
  you change your password. It is never written elsewhere or logged and it is
  never given to an AI agent.
- **Your templates travel with your vault.** bwtidy keeps your templates and
  datatypes in a secure note named `bwtidy` along with the generator settings.
  You can rename the note and bwtidy will still find it.
- **Accounts are kept apart.** Each account runs in its own process under its
  own Linux user. One account cannot read another account's data.
- **You control changes.** In the web UI you make changes directly. An AI agent
  connected through MCP can add proposed changes to a queue without seeing
  secret values. You review each proposal and choose whether to accept it. Only
  accepted proposals are written to your vault.
- **No telemetry.** bwtidy sends no telemetry to its author or anyone else. Its
  vault connection is to your Bitwarden or Vaultwarden server.

```graphviz alt="bwtidy runs in one container on your machine. Your edits in the web UI and the proposals of an AI agent over MCP wait in its queue, and the agent sees no secret values. You approve each proposal in the web UI before it is written. Before each write bwtidy saves an encrypted backup to its backups volume and then writes through the Bitwarden CLI to your Bitwarden or Vaultwarden server."
digraph {
  nodesep=0.3
  ranksep=0.4
  fontname="sans-serif"
  node [fontname="sans-serif" fontsize=13 shape=box style="rounded,filled" fillcolor="#ffffff" color="#5c6bc0" margin="0.12,0.06"]
  edge [fontname="sans-serif" fontsize=12 color="#455a64"]
  you [label="You" fillcolor="#e8eaf6"]
  agent [label=<AI agent<BR/><FONT POINT-SIZE="11" COLOR="#bf360c">sees no secret values</FONT>> fillcolor="#fff3e0" color="#e65100" style="rounded,filled,dashed"]
  subgraph cluster_machine {
    label="Your machine" labeljust=l labelloc=b style="rounded,dashed" color="#2e7d32" fontcolor="#1b5e20"
    subgraph cluster_bwtidy {
      label="bwtidy container" labeljust=r labelloc=b style="rounded,filled" fillcolor="#f1f8e9" color="#558b2f" fontcolor="#33691e"
      ui [label="Web UI"]
      queue [label="Queue"]
      bw [label="bw\nBitwarden's official CLI"]
    }
    backups [label="Backups volume" shape=cylinder]
  }
  server [label="Bitwarden or\nVaultwarden\nserver" shape=cylinder fillcolor="#e3f2fd" color="#1565c0"]
  {rank=same; you; agent}
  you -> ui [label="edit and\napprove"]
  agent -> queue [label="propose\nover MCP"]
  ui -> queue [label="edits and\napprovals"]
  queue -> backups [label="encrypted backup\nbefore each write"]
  queue -> bw [label="write"]
  bw -> server [label="write"]
}
```

How each part works is under [Explanation](#explanation).

## Tutorial

The tutorials build on each other, so take them in order. Each one after
the first starts from the vault as the one before it left it.

- In [Your first cleanup](tutorial/first-cleanup.md) you log in and fix a
  name and a field. Then you commit and find the write and its backup in the
  Change log.
- In [Your first template](tutorial/template.md) you make a datatype and a
  template. The grid shows how well each item fits the template and each
  value its datatype. Then you use the template to turn notes into logins.
- In [Your first MCP-assisted cleanup](tutorial/mcp.md) you make an MCP
  token and connect your agent. Then you approve its proposals on the Queue
  page.

## How-to guides

- [Run bwtidy](how-to/run.md) shows how to start the container from the
  image on Docker Hub or from one you build yourself. It also covers the
  first login and updates.
- [Connect an MCP client](how-to/mcp.md) shows how to make a token and add
  bwtidy to your agent. It covers Claude Code and Codex as well as Copilot
  CLI and Gemini CLI.
- [Show or hide columns](how-to/show-hide-columns.md) in the grid and pick
  the columns for a merge.
- [Rename an attachment](how-to/rename-attachment.md) of an item.
- [Restore a vault from a backup](restore.md) into an empty account without
  bwtidy. All you need is age and Python 3 along with bash and the Bitwarden
  CLI.

## Reference

- [The web UI](reference/ui.md) describes every page, menu and button.
- [MCP tools](reference/mcp-tools.md) lists every tool an AI agent can call
  with its parameters.
- [Configuration](reference/configuration.md) lists every environment
  variable and where bwtidy keeps its files.
- [Release notes](releases.md) say what each version brings.

## Explanation

- [How bwtidy fits together](explanation/overview.md) shows the gateway and
  the worker it starts for an account when you log in. It also covers the
  Bitwarden CLI and where your data is.
- [Logging in](explanation/login.md) goes through the account, the Bitwarden
  CLI login and the unlock. It also explains the session and what the master
  password is used for.
- [How edits are saved](explanation/edits.md) follows an edit through the
  queue and the checks to the backup before every write. It also covers the
  Change log and how Restore undoes a write.
- [Secret values and backups](explanation/secrets.md) says which values are
  secret and where they never go. It also says what a backup holds.
- [What an agent can do](explanation/mcp.md) explains what an agent sees and
  what it can propose. Nothing it proposes is written until you approve it
  on the Queue page.
- [The bwtidy note](explanation/tool-note.md) describes the note that bwtidy
  keeps in your vault and what it holds.
- [How bwtidy uses the Bitwarden CLI](explanation/bw-cli.md) shows how
  `bw serve` does the reads and writes. Single `bw` commands handle the login
  and the backups.
- [How accounts are kept apart](explanation/isolation.md) explains the Linux
  uid that each account runs as and what root can still see.

## For contributors

- [Contributing](contributing.md) says how to report a bug or ask for a
  change. It also covers how to improve the docs, make a pull request and
  report a security issue.
- [The spec](spec.md) holds the rules and reasons that these pages do not
  state.
- [The architecture](architecture.md) maps the code and its layers and says
  what each layer may import. It also shows who writes each kind of data and
  how a write flows.

The developer guide lives in the repository outside this site.
`dev/README.md` explains the setup and the commands. `dev/dev-vault.md`
covers the dev stack and `dev/testing.md` the tests and the docs
screenshots. `dev/release.md` covers the docs site and the image.

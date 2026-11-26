# bwtidy documentation

## Beta software

> **Beta software, made with AI.** bwtidy was written entirely by AI coding
> agents. Several advanced models (Claude Fable, Opus and Sonnet) built it in
> many small steps under the author's guidance, and Codex (GPT-6.2-Astra)
> reviewed it for security and stability. No human has read every line of the
> code. The author uses it for his own real passwords, but it has very few
> users so far. It changes the contents of your password vault, so keep your
> own Bitwarden export, made outside bwtidy, and expect bugs and breaking
> changes between versions.

<!-- intro: the same text in README.md and docs/index.md -->

A password vault gets messy over the years: `Password` in some items,
`password` in others, addresses without `https://`. Bitwarden's apps show
one item at a time, so this is hard to see and fix. bwtidy shows the whole
vault in one grid. Tidy means items of one kind share field names and
formats: a template says which fields, a datatype what a value looks like.
An AI agent can propose fixes over MCP and never sees a secret value. You
approve every change, and bwtidy backs up the vault before each write. It
is for people who keep a large Bitwarden or Vaultwarden vault, often
self-hosted.

<!-- end of intro -->

New to bwtidy? [Run it](how-to/run.md), then take [the tutorial](tutorial/first-cleanup.md).

## At a glance

- **Your vault stays on your server.** bwtidy runs in one container on your
  own machine. It reads and writes your vault on your Bitwarden or
  Vaultwarden server only through `bw`, Bitwarden's official command-line
  tool, exactly as Bitwarden publishes it.
- **A backup before every change.** Before each write bwtidy saves a backup
  of the whole vault: a zip file encrypted with [age](https://age-encryption.org),
  with your master password as the passphrase. The backups are in bwtidy's
  backups volume, and you can restore one with standard tools, without
  bwtidy.
- **Your master password.** It stays in memory while you are logged in, and
  is wiped when you log out, 2 minutes after you close the last tab or after
  2 hours without activity (both by default). The one stored copy is inside
  your own vault, in bwtidy's note, so older backups stay readable after you
  change your master password. It is never written anywhere else, never
  logged and never given to an AI agent.
- **One item of its own in your vault.** bwtidy keeps its settings in one
  item, a secure note named `bwtidy`. You can rename it, and bwtidy still
  finds it.
- **Accounts are kept apart.** Each account is handled by its own process,
  running as its own Linux user, so one account cannot read another's data.
- **AI agents propose, you decide.** An AI agent connected over MCP never
  sees secret values, and nothing it proposes is written until you approve
  it.
- **No telemetry.** bwtidy sends nothing to its author or anyone else. The
  only connection it makes is to your own Bitwarden server.

How each part works is under [Explanation](#explanation).

## Tutorial

Take them in order: each continues from the vault the one before left.

- [Your first cleanup](tutorial/first-cleanup.md): log in, fix a name and a
  field, commit, and find the write and its backup in the Change log.
- [Your first template](tutorial/template.md): make a datatype and a
  template, see the fill scores and format marks in the grid, and turn notes
  into logins with the template.
- [Your first MCP-assisted cleanup](tutorial/mcp.md): make an MCP token, connect
  your agent, and approve its proposals in the Queue.

## How-to guides

- [Run bwtidy](how-to/run.md): start the container from the image on Docker
  Hub or build it yourself, log in the first time, update.
- [Connect an MCP client](how-to/mcp.md): make a token and add bwtidy to
  Claude Code, Codex, Copilot CLI or Gemini CLI.
- [Show or hide columns](how-to/show-hide-columns.md) in the grid.
- [Rename an attachment](how-to/rename-attachment.md).
- [Restore a vault from a backup](restore.md), with only age, Python 3, bash and
  the Bitwarden CLI.

## Reference

- [The web UI](reference/ui.md): every page, menu and button.
- [MCP tools](reference/mcp-tools.md): every tool an AI agent can call, with
  its parameters.
- [Configuration](reference/configuration.md): every environment variable
  and where bwtidy keeps its files.
- [Release notes](releases.md): what each version brings.

## Explanation

- [How bwtidy fits together](explanation/overview.md): the gateway, a
  worker per account, the Bitwarden CLI and where your data is.
- [Logging in](explanation/login.md): the account, the Bitwarden CLI login,
  the unlock, the session and what the master password is used for.
- [How edits are saved](explanation/edits.md): the queue, the checks, the
  backup before every write, the change log and Restore.
- [Secret values and backups](explanation/secrets.md): what is secret, where
  it never goes, and what a backup holds.
- [What an agent can do](explanation/mcp.md): what it sees, what it
  proposes, and your approval in the Queue.
- [The bwtidy note](explanation/tool-note.md): the note bwtidy keeps in your
  vault and what it holds.
- [How bwtidy uses the Bitwarden CLI](explanation/bw-cli.md): `bw serve` for
  reads and writes, the CLI for the login and the backups.
- [How accounts are kept apart](explanation/isolation.md): a Linux uid per
  account, and what root can still see.

## For contributors

- [Contributing](contributing.md): report a bug, ask for a change, improve
  the docs, make a pull request or report a security issue.
- [The spec](spec.md): the rules and reasons these pages do not state.
- [The architecture](architecture.md): the map of the code, its layers and
  what each may import, who writes each kind of data and how a write flows.
- The developer guide in the repository: `dev/README.md` to set up and for
  the commands, `dev/dev-vault.md` for the dev stack, `dev/testing.md` for
  the tests and the docs screenshots, `dev/release.md` for the docs site and
  the image.

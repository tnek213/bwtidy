# bwtidy documentation

## Alpha software

> **Alpha software.** Most of bwtidy was written with AI coding agents ("vibe
> coded") and it has not been reviewed or audited by anyone else. It changes
> the contents of your password vault. Do not use it in production or on a
> vault you cannot afford to lose. If you try it anyway, keep your own
> Bitwarden export, made outside bwtidy, and expect bugs and breaking changes
> between versions.

bwtidy is a web UI and MCP server for keeping a Bitwarden vault tidy. It shows
the whole vault as one wide grid, helps you normalize field names and apply
templates, and lets an AI agent propose changes over MCP (the Model Context
Protocol, how AI agents call tools) without seeing secret values. Nothing an
agent proposes is written until you approve it.

New to bwtidy? [Run it](how-to/run.md), then take [the tutorial](tutorial/first-cleanup.md).

## Tutorial

- [Your first cleanup](tutorial/first-cleanup.md): log in, fix a name and a
  field, commit, and find the write and its backup in the Change log.

## How-to guides

- [Run bwtidy](how-to/run.md): build and start the container, log in the
  first time, update.
- [Connect an MCP client](how-to/mcp.md): make a token and add bwtidy to
  Claude Code, Codex, Copilot CLI or Gemini CLI.
- [Show or hide columns](how-to/show-hide-columns.md) in the grid.
- [Rename an attachment](how-to/rename-attachment.md).
- [Restore a vault from a backup](restore.md), with only age, unzip, jq and
  the Bitwarden CLI.

## Reference

- [The web UI](reference/ui.md): every page, menu and button.
- [MCP tools](reference/mcp-tools.md): every tool an AI agent can call, with
  its parameters.
- [Configuration](reference/configuration.md): every environment variable
  and where bwtidy keeps its files.

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

- [The spec](spec.md): what bwtidy does and why, every rule with its reason.
- [The architecture](architecture.md): the map of the code, its layers and
  what each may import, who writes each kind of data and how a write flows.
- `CONTRIBUTING.md` in the repository: where to start, and how to report a
  bug, ask for a feature or report a security issue (`SECURITY.md`).
- The developer guide in the repository: `dev/README.md` to set up and for
  the commands, `dev/dev-vault.md` for the dev stack, `dev/testing.md` for
  the tests and the docs screenshots, `dev/release.md` for the docs site and
  the image.

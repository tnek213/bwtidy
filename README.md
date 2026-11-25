# bwtidy

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

![The bwtidy grid with the vault's items as rows and their fields as columns](docs/images/vault-grid.png)

> **Alpha software, made with AI.** bwtidy was written entirely by AI coding
> agents. Several advanced models (Claude Fable, Opus and Sonnet) built it in
> many small steps under the author's guidance, and Codex (GPT-6.2-Astra)
> reviewed it for security and stability. No human has read every line of the
> code. The author uses it for his own real passwords, but it has very few
> users so far. It changes the contents of your password vault, so keep your
> own Bitwarden export, made outside bwtidy, and expect bugs and breaking
> changes between versions.

Does bwtidy work for you? Give [Works for me](https://github.com/tnek213/bwtidy/discussions/16) a thumbs up.
If you have a minute, a comment with your bwtidy version, Bitwarden or
Vaultwarden, and roughly how many items your vault has helps others decide.

bwtidy is an independent project. It is not affiliated with, endorsed by or
sponsored by Bitwarden, Inc. Bitwarden is a trademark of Bitwarden, Inc., used
here only to say which vaults bwtidy works with.

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

How each part works: [Explanation](docs/index.md#explanation).

## Running

bwtidy runs as one container with the official Bitwarden CLI inside. It
publishes its web page on port 8765 of the host's 127.0.0.1 and keeps each
account's data in a volume at `/data/accounts`, and its backups at
`/backups`.

bwtidy runs the Bitwarden CLI exactly as Bitwarden publishes it and follows
its new releases: see
[Which version of the CLI](docs/explanation/bw-cli.md#which-version-of-the-cli).

How to run it: [Run bwtidy](docs/how-to/run.md), from pulling the image to
the first login, a reverse proxy and updates.

## Documentation

The docs are a site at https://tnek213.github.io/bwtidy/. A running bwtidy
serves the same site at `/docs/`, such as http://127.0.0.1:8765/docs/,
without a login.

- [The documentation site](https://tnek213.github.io/bwtidy/)
- [Documentation home](docs/index.md)
- [Your first cleanup](docs/tutorial/first-cleanup.md): a tutorial from the
  login to a first write and its backup
- [How-to guides](docs/index.md#how-to-guides): run bwtidy, connect an MCP
  client, restore a backup and more
- [Reference](docs/index.md#reference): the web UI, the MCP tools and the
  configuration
- [Explanation](docs/index.md#explanation): how bwtidy works and what it does
  to your vault
- [Release notes](docs/releases.md): what each version brings

## Contributing

- [CONTRIBUTING.md](CONTRIBUTING.md): where to start, and how to report a bug or
  ask for a feature
- [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md): how everyone taking part behaves, and
  where to report what breaks it
- [SECURITY.md](SECURITY.md): how to report a security issue, privately
- [The developer guide](dev/README.md): set up, the dev vault, the tests, releasing
- [AGENTS.md](AGENTS.md): the rules every change follows and the checks it
  must pass
- [The architecture](docs/architecture.md): where the code lives
- [The spec](docs/spec.md): the rules and reasons the user docs do not state

## License

Copyright (C) 2026 Kent Larsson

bwtidy is free software: you can redistribute it and/or modify it under the
terms of version 3 of the GNU Affero General Public License as published by
the Free Software Foundation.

bwtidy is distributed in the hope that it will be useful, but WITHOUT ANY
WARRANTY, without even the implied warranty of MERCHANTABILITY or FITNESS FOR
A PARTICULAR PURPOSE. See the GNU Affero General Public License for more
details. The full text is in LICENSE.

bwtidy changes the contents of a password vault. Keep your own Bitwarden
export, made outside bwtidy, before you use it on a vault you care about.

The container image includes the official Bitwarden CLI, which Bitwarden, Inc.
licenses separately under its own terms (GPL-3.0).

The About page (About bwtidy in the app bar's menu, More actions) shows the
license, links the source code and thanks the projects bwtidy is built on,
with every third-party package bwtidy ships and its license.

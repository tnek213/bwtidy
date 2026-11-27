# bwtidy

<!-- intro: the same text in README.md, docs/index.md and
docker/hub-overview.md -->

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
The backup is an age-encrypted ZIP stored in bwtidy's backups volume. You can
restore it with standard tools. Your vault stays on your Bitwarden or
Vaultwarden server. bwtidy reads and writes vault data through Bitwarden's
official CLI.

bwtidy is for people who want their Bitwarden or Vaultwarden vault to follow
standards they define themselves. It helps them apply those standards
consistently across the vault.

<!-- end of intro -->

![The bwtidy grid with the vault's items as rows and their fields as columns](docs/images/vault-grid.png)

> [!WARNING]
> **Made with AI.** AI coding agents wrote all of bwtidy under the author's
> guidance. Claude Fable, Opus and Sonnet built it in many small steps. Codex
> (GPT-6.2-Astra) reviewed it for security and stability. No human has read
> every line. The author uses it for real passwords but the project has few
> users so far. Because bwtidy changes your vault keep an independent Bitwarden
> export made outside bwtidy. Expect bugs and breaking changes between
> versions.

Does bwtidy work for you? Give [Works for
me](https://github.com/tnek213/bwtidy/discussions/16) a thumbs up. If you have a
minute, a comment with your bwtidy version, Bitwarden or Vaultwarden, and
roughly how many items your vault has helps others decide.

bwtidy is an independent project. It is not affiliated with, endorsed by or
sponsored by Bitwarden, Inc. Bitwarden is a trademark of Bitwarden, Inc., used
here only to say which vaults bwtidy works with.

## At a glance

- **Your vault stays on your server.** bwtidy runs in one container on your
  machine. It reads and writes your vault through `bw`, Bitwarden's official
  CLI.
- **A backup before your changes are written.** bwtidy saves a backup of the
  whole vault before each write of your changes. The backup is a ZIP encrypted
  with age using your master password as the passphrase. It lives in bwtidy's
  backups volume and can be restored with standard tools without bwtidy.
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

[Explanation](docs/index.md#explanation) says how each part works.

## Running

bwtidy runs as one container with the official Bitwarden CLI inside. It
publishes its web page on port 8765 of the host's 127.0.0.1 and keeps each
account's data in a volume at `/data/accounts`, and its backups at
`/backups`.

bwtidy ships the open source build of the Bitwarden CLI and follows its new
releases (see
[Which version of the CLI](docs/explanation/bw-cli.md#which-version-of-the-cli)).

[Run bwtidy](docs/how-to/run.md) shows how to run it. It goes from pulling
the image to the first login and also covers a reverse proxy and updates.

The image is
[tnek213/bwtidy on Docker Hub](https://hub.docker.com/r/tnek213/bwtidy).
Its overview lists the tags, the port, the volumes and the settings.

## Documentation

The docs are a site at <https://tnek213.github.io/bwtidy/>. A running bwtidy
serves the same site at `/docs/`, such as <http://127.0.0.1:8765/docs/>,
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
- [ACCESSIBILITY.md](ACCESSIBILITY.md): what works with a keyboard and a screen
  reader, what does not, and how to report a barrier
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

The container image includes the open source build of the Bitwarden CLI.
Bitwarden, Inc. licenses it separately under GPL-3.0. The image builds it from
Bitwarden's source (<https://github.com/bitwarden/clients>) at the tag
`cli-v2026.11.0`. Bitwarden's npm package is not used because it includes code
under the Bitwarden License. The CLI bundles Bitwarden's SDK (also GPL-3.0)
from <https://github.com/bitwarden/sdk-internal>. The file `SOURCE` beside the
CLI in the image names the commit of each.

The About page (About bwtidy in the app bar's menu, More actions) shows the
license, links the source code and thanks the projects bwtidy is built on,
with every third-party package bwtidy ships and its license.

# Release notes

The versions of bwtidy follow [Semantic Versioning](https://semver.org) with
`-alpha.N`, `-beta.N` and `-rc.N` pre-releases. [Road to
1.0](explanation/road-to-1.0.md) says what the beta needs before 1.0.

## Unreleased

Changes on main that are not yet in a versioned Docker image.

Nothing yet.

## 1.0.0-beta.3

2026-11-28 · [GitHub release](https://github.com/tnek213/bwtidy/releases/tag/v1.0.0-beta.3)

1.0.0-beta.3 is the first release whose image builds Bitwarden's open source
CLI from source, and it replaces every earlier image. It also holds the
changes of 1.0.0-beta.2, which was never published. Your stored data needs no
migration from 1.0.0-beta.1: act on the breaking changes below, then pull the
new image and start it.

### Breaking changes

- Pull `1.0.0-beta.3` or `latest` if you run an earlier image. Every earlier
  image is withdrawn from Docker Hub: 0.1.0-alpha.1, 1.0.0-beta.1 and each
  `main` and commit tag from before this release. They carried Bitwarden's npm
  build of the Bitwarden CLI, which includes code under the Bitwarden License.
  The GitHub releases and tags of 0.1.0-alpha.1 and 1.0.0-beta.1 are withdrawn
  too, and their entries below stay as a record
  ([#195](https://github.com/tnek213/bwtidy/pull/195), [#197](https://github.com/tnek213/bwtidy/pull/197),
  [#231](https://github.com/tnek213/bwtidy/pull/231))
- Check `BWTIDY_MAX_PENDING`, `BWTIDY_MAX_WORKERS` and `BWTIDY_UID_BASE` in
  `.env` before you start the new image. The gateway now refuses to start when
  a number setting is not a whole number in its range, and the error names the
  setting ([#143](https://github.com/tnek213/bwtidy/pull/143))

### Other changes

- Run bwtidy with Bitwarden's open source build of the Bitwarden CLI, which
  the image now builds from Bitwarden's source
  ([#195](https://github.com/tnek213/bwtidy/pull/195))
- Point every worker at a Bitwarden CLI of your own with `BWTIDY_BW_BIN`.
  [Run your own Bitwarden
  CLI](explanation/bw-cli.md#run-your-own-bitwarden-cli) says how and what you
  take on ([#191](https://github.com/tnek213/bwtidy/pull/191))
- Use the web UI with a screen reader or a keyboard. The
  [accessibility statement](https://github.com/tnek213/bwtidy/blob/main/ACCESSIBILITY.md)
  says what works and how to report a barrier
  ([#166](https://github.com/tnek213/bwtidy/pull/166), [#167](https://github.com/tnek213/bwtidy/pull/167))
- Turn on [usage stats](reference/configuration.md#usage-stats) that stay on
  your machine with `BWTIDY_STATS=1`. They are off by default and kept on
  their own volume (`bwtidy-stats`)
  ([#96](https://github.com/tnek213/bwtidy/pull/96), [#174](https://github.com/tnek213/bwtidy/pull/174))
- Get organization items back in their folders and with their favorite marks
  when you restore a backup. The restore page promises only what a restore
  brings back ([#190](https://github.com/tnek213/bwtidy/pull/190))
- Create and convert secure notes through MCP with the type `note`, the name
  the MCP tools list them by
  ([#177](https://github.com/tnek213/bwtidy/pull/177))
- See a name clash in the attachment and folder dialogs and the template
  editor before you save. They compare names without regard to case
  ([#172](https://github.com/tnek213/bwtidy/pull/172), [#196](https://github.com/tnek213/bwtidy/pull/196))
- Run a demo of bwtidy against its own Vaultwarden, with limits on what its
  containers can reach and use. [Run a public demo](how-to/run-demo.md) says
  what a public one needs
  ([#47](https://github.com/tnek213/bwtidy/pull/47), [#147](https://github.com/tnek213/bwtidy/pull/147),
  [#149](https://github.com/tnek213/bwtidy/pull/149), [#151](https://github.com/tnek213/bwtidy/pull/151),
  [#164](https://github.com/tnek213/bwtidy/pull/164))
- Restore a vault from a backup on Windows with PowerShell
  ([#230](https://github.com/tnek213/bwtidy/pull/230))
- Refuse an email or a server too long for an account's directory name with
  a clear message, where the first login failed with an error before
  ([#227](https://github.com/tnek213/bwtidy/pull/227))
- Say in the docs and in the description of the MCP tool list_items that
  bwtidy shows only the items that are not archived
  ([#225](https://github.com/tnek213/bwtidy/pull/225))
- Find the archived items of an account without premium, with their
  attachments, in every backup
  ([#224](https://github.com/tnek213/bwtidy/pull/224))
- Restore a change of an item you archived since, which then stays archived,
  and keep a folder that still holds archived items from being deleted
  ([#220](https://github.com/tnek213/bwtidy/pull/220))
- Find archived items and their attachments in every backup, those in the
  trash too. A restore brings their attachments back
  ([#201](https://github.com/tnek213/bwtidy/pull/201))
- Run version 2026.11.0 of the Bitwarden CLI, which the image builds from
  Bitwarden's source ([#108](https://github.com/tnek213/bwtidy/pull/108))
- Read the usage stats with `bwtidy stats` in the container as the docs say.
  The image did not find the command before
  ([#209](https://github.com/tnek213/bwtidy/pull/209))

## 1.0.0-beta.1

2026-11-26 · Withdrawn on 2026-11-27, see
[1.0.0-beta.3](#100-beta3)

bwtidy is feature complete for 1.0 and enters its beta. From this version on,
a stored format changes only with a migration. [Road to
1.0](explanation/road-to-1.0.md) says what the beta needs before 1.0. If
bwtidy works for you, say so in the
[Works for me discussion](https://github.com/tnek213/bwtidy/discussions/16).

- Keep a new name for the bwtidy note with Keep this name, which the warning
  about a renamed note now offers next to Rename it back
  ([#21](https://github.com/tnek213/bwtidy/pull/21))
- Learn templates with datatypes in [Your first
  template](tutorial/template.md) and a cleanup by an AI agent over MCP in
  [Your first MCP-assisted cleanup](tutorial/mcp.md). Each tutorial builds on
  the one before
  ([#23](https://github.com/tnek213/bwtidy/pull/23), [#35](https://github.com/tnek213/bwtidy/pull/35),
  [#43](https://github.com/tnek213/bwtidy/pull/43))
- Restore a backup with only age, Python 3 and the Bitwarden CLI installed
  ([#19](https://github.com/tnek213/bwtidy/pull/19))
- Pull a smaller image, built on Debian 13 (trixie) with neither npm nor pip.
  Every image still has a signed provenance attestation
  ([#25](https://github.com/tnek213/bwtidy/pull/25), [#39](https://github.com/tnek213/bwtidy/pull/39))
- Count on a daily scan of the `latest` and `main` images for known
  vulnerabilities ([#20](https://github.com/tnek213/bwtidy/pull/20))

## 0.1.0-alpha.1

2026-11-25 · Withdrawn on 2026-11-27, see
[1.0.0-beta.3](#100-beta3)

The first release is a web UI and MCP server for a Bitwarden or Vaultwarden
vault. It shows the vault as one grid, normalizes field names and applies
templates. An AI agent can propose changes that you approve, with an encrypted
backup before every write.

How bwtidy was written and reviewed is in the
[README](https://github.com/tnek213/bwtidy#readme).

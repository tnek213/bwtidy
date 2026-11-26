# Release notes

What each version of bwtidy brings, newest first. How to run a version is in
[Run bwtidy](how-to/run.md).

## Road to 1.0

The beta started with 1.0.0-beta.1. bwtidy is feature complete for 1.0, and
the beta is for finding bugs and for feedback from real vaults. From the
beta on, a stored format (the database, the bwtidy note, the backups, the
settings) changes only with a migration.

The beta becomes a release candidate when all of these hold:

- At least 10 "Works for me" reports from different people in the
  [Works for me discussion](https://github.com/tnek213/bwtidy/discussions/16),
  with Bitwarden and Vaultwarden each at least once.
- 8 weeks of real use with no bug that lost or damaged vault data, and no
  open security report. The 8 weeks count from the first "Works for me"
  report by someone other than the author, not from the release, so they
  measure use rather than time.
- No serious bug open.
- An upgrade from one beta to the next done with a migration, and tested.
- Docker Hub pulls of at least 0.01% of the pulls of `vaultwarden/server`,
  measured on the day of the decision.

The release candidate becomes 1.0.0 after another 8 weeks with no serious
bug, counted from the release candidate. Patch releases (1.0.0-beta.2, 1.0.0-beta.3 and so on) come whenever
something is fixed or the Bitwarden CLI has a new release.

## 1.0.0-beta.1

bwtidy is feature complete for 1.0 and in beta: from this version on, a
stored format changes only with a migration. What the beta needs before 1.0
is in [Road to 1.0](#road-to-10). If bwtidy works for you, say so in the
[Works for me discussion](https://github.com/tnek213/bwtidy/discussions/16).

What changed since 0.1.0-alpha.1:

- The bwtidy note can keep a new name: the warning about a renamed note
  offers Keep this name next to Rename it back.
- New tutorials for templates with datatypes and for a first cleanup with
  an AI agent over MCP, each one building on the one before.
- Restoring a backup needs only age, Python 3 and the Bitwarden CLI, and
  the steps cover Windows and macOS too.
- The docs site is wider, keeps text at a readable width, opens a screenshot
  larger on a click and links every page to a form for suggesting an
  improvement.
- The image builds on Debian 13 (trixie) and holds neither npm nor pip, so
  it is smaller. Every image still has a signed provenance attestation, and
  the published images are scanned for known vulnerabilities every day.
  Corrected: the scan covers the `latest` and `main` images, not every
  published tag.

## 0.1.0-alpha.1

The first release: a web UI and MCP server that shows a Bitwarden or
Vaultwarden vault as one grid, normalizes field names, applies templates and
lets an AI agent propose changes that you approve, with an encrypted backup
before every write.

This first version was written by AI, using Claude, over many small steps. I
gave technical guidance, UI improvements and feature tweaks along the way, but
no human has read every line of the code. It has been reviewed for security
and stability by Codex (GPT-6.2-Astra) and Claude Fable 5.1, over three
rounds.

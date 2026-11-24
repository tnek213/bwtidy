# Contributing

bwtidy is alpha software, so expect changes: features, schemas and
behavior move between versions. Issues and changes are welcome, but there is
no promise of a review time or of a change going in.

## Where to start

- [dev/README.md](dev/README.md): set up and the commands, with links to
  the dev stack with its own test vault ([dev/dev-vault.md](dev/dev-vault.md))
  and the tests ([dev/testing.md](dev/testing.md)).
- [docs/architecture.md](docs/architecture.md): where the code lives and how it
  fits together. A change follows it. Changing the structure itself is a
  proposal: open an issue first.
- [AGENTS.md](AGENTS.md): the rules every change follows, for people and
  agents alike.
- [docs/spec.md](docs/spec.md): what bwtidy does and why. A change to what a
  user sees updates it in the same commit.
- `scripts/ci`: runs the lint, type, build and unit checks. It must pass
  before a change goes in. The CI workflow (`.github/workflows/ci.yml`) runs
  it on every push to main and every pull request.

Work only against the dev stack, never against a real vault.

## Reporting a bug or asking for a feature

Open an issue and pick a form: "Bug report" or "Feature request". Never paste
vault contents, secrets, tokens or backups into an issue, describe them
instead.

## Reporting a security issue

Do not open an issue. Follow [SECURITY.md](SECURITY.md).

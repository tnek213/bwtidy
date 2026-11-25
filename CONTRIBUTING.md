# Contributing

bwtidy is alpha software, so expect changes: features, schemas and
behavior move between versions. It has one maintainer for now and is meant to
grow in the open, so issues, ideas and changes are welcome. There is no
promise of a review time or of a change going in.

Everyone taking part follows the [code of conduct](CODE_OF_CONDUCT.md). Report
behavior that breaks it to tnek213.recollect098@slmail.me.

## Before you start on a feature

Discuss a new feature first, in a "Feature request" issue or under Ideas in
the Discussions, before you write it. Not every feature fits what bwtidy is
for, and talking first saves you work on a change that will not go in. A bug
fix or a docs fix can go straight to a pull request.

If a feature you want does not fit, you are welcome to fork: the license
allows it.

## Where to start

- [dev/README.md](dev/README.md): set up and the commands, with links to
  the dev stack with its own test vault ([dev/dev-vault.md](dev/dev-vault.md))
  and the tests ([dev/testing.md](dev/testing.md)).
- [docs/architecture.md](docs/architecture.md): where the code lives and how it
  fits together. A change follows it. Changing the structure itself is a
  proposal: open an issue first.
- [AGENTS.md](AGENTS.md): the rules every change follows, for people and
  agents alike.
- [docs/spec.md](docs/spec.md): what bwtidy does and why. You are welcome to
  update it with your change but do not have to: the maintainer keeps it in
  step. A change to what a user sees does update the page under `docs/` that
  covers it, in the same commit.
- `scripts/ci`: runs the lint, type, build and unit checks. It must pass
  before a change goes in. The CI workflow (`.github/workflows/ci.yml`) runs
  it on every push to main and every pull request.
- `scripts/install-hooks`: run it once after cloning. It installs a pre-push
  hook that runs `scripts/ci` on what you push to main (to any other branch
  only `scripts/ci --quick`, the pull request's CI runs the rest) and, for a
  change to the UI, takes the docs screenshots again and commits those that changed
  ([dev/testing.md](dev/testing.md#the-pre-push-hook)).

Work only against the dev stack, never against a real vault.

The pull request template (`.github/pull_request_template.md`) holds the
checklist for a pull request.

Dependencies change only through the lock files (`backend/uv.lock`,
`frontend/package-lock.json`, `docker/bitwarden-cli/package-lock.json`):
`uv lock` or `uv add` in `backend/`, `npm install` in the npm folder, and the
changed lock file goes into the change. Every build and test uses them locked
and fails when one is out of date. `scripts/update-deps` updates every
dependency within its declared range and lists the updates beyond it. [AGENTS.md](AGENTS.md) has the details.

## Commits

Write commit messages as https://cbea.ms/git-commit/ describes:

- A subject line in the imperative mood ("Add", "Fix", "Explain"), capitalized,
  without a period at the end, about 50 characters and at most 72.
- A body only when the change needs more detail or its motivation explained.
  Leave a blank line after the subject and wrap the body at 72 characters. It
  says what changed and why, the diff shows how.

bwtidy does not use Conventional Commits: no `feat:`, `fix:` or other prefixes
in the subject.

Sign your commits if you can (SSH or GPG, so GitHub shows them as
Verified). A pull request whose commits are all signed and tidy may be merged
with its commits kept. Otherwise it is squashed into one commit, which
GitHub signs.

## License of your contribution

bwtidy is licensed under the GNU Affero General Public License, version 3 only
(`LICENSE`). By opening a pull request you agree that your contribution is
published under that same license, and you confirm that you have the right to
submit it.

## Reporting a bug or asking for a feature

Open an issue and pick a form: "Bug report" or "Feature request". Never paste
vault contents, secrets, tokens or backups into an issue, describe them
instead.

## Reporting a security issue

Do not open an issue. Follow [SECURITY.md](SECURITY.md).

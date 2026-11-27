# Releasing

How a pull request is merged into main, the version and its release notes,
releasing a version, the documentation site and its publishing on GitHub
Pages, and publishing the image.

## Merging pull requests

The ruleset "Protect main" (Settings, Rules, Rulesets) guards main. Its bypass
list is empty, so its rules hold for the maintainer too:

- Restrict deletions and Block force pushes: the history of main is never
  lost or rewritten.
- Require a pull request before merging, with no approvals since there is
  one maintainer: every change goes through a pull request and its CI.
- Require status checks to pass, with the check `ci`: the job of
  `.github/workflows/ci.yml` that runs `scripts/ci`.
- Require signed commits: every commit on main shows as Verified. GitHub
  signs the merge commits and squash commits it makes itself.

The repository allows merge commits and squash merging, both with the pull
request's title as the subject and an empty body. Rebase merging stays off,
because GitHub cannot sign the commits a rebase merge writes.

The merge method follows from the pull request's commits:

- All signed: merge with a merge commit ("Create a merge commit" in the web
  UI, or `gh pr merge N --merge`). The contributor's commits land unchanged
  with the merge commit on top, so their own squash stays one commit.
- At least one unsigned: squash with `gh pr merge N --squash --admin`, which
  makes one commit that GitHub signs.

The web UI cannot merge the second kind. It checks the pull request's own
commits, shows "Merging is blocked" with "Commits must have verified
signatures" and disables Squash and merge. GitHub applies the rule to the
commits that reach main, and a squash brings only its own signed commit.
`gh pr merge` makes the web UI's check before it asks GitHub to merge, and
`--admin` skips only that check. With no bypass list GitHub still enforces
every rule: the merge fails while `ci` has not passed, and a merge commit
that would bring an unsigned commit onto main fails with "Commits must have
verified signatures".

A bypass of Require signed commits for the admin role would enable Squash
and merge in the web UI. It would also let a merge commit bring unsigned
commits onto main without a warning, even from `gh pr merge N --merge`
without `--admin`. That is why the rule has no bypass.

### The CLI pin of Dependabot's pull requests

Dependabot's pull request for a new Bitwarden CLI changes only
`docker/bitwarden-cli/package.json` and its lock file. The workflow
"Dependabot CLI pin" (`.github/workflows/dependabot-cli-pin.yml`) adds the rest
by itself. It runs `scripts/update-deps --cli-pin`, which writes the tag and
the commit in `docker/bitwarden-cli/source.json` and the version in the docs
that state it. It commits them as `github-actions[bot]` with the subject "Pin
the Bitwarden CLI source to cli-vVERSION".

The workflow makes the commit with its `GITHUB_TOKEN`, so GitHub holds the
`pull_request` runs of that commit until someone with write access approves
them. Click "Approve workflows to run" in the merge box. Once `ci` passes on
the new commit you merge with a merge commit as usual.

With `--pr N`, the number of the pull request that the workflow passes, the
script also writes the pull request's release note under `## Unreleased` in
`docs/releases.md`: the bullet "Run the Bitwarden CLI VERSION in the image"
with the link to pull request N. It takes the place of "Nothing yet." and is
written once: a run again changes nothing, and a bullet of the same pull
request for another version is replaced. So the pull request passes the check
of its release note
([The release note of a pull request](#the-release-note-of-a-pull-request))
once the commit is in. Dependabot's other pull requests have the labels
`dependencies` and `no release note` (`.github/dependabot.yml`), as the
release notes leave a routine bump out. Setting labels replaces Dependabot's
default `dependencies`, so the list keeps it. The pull requests for the CLI
keep that default alone.

Dependabot's commits and the workflow's commit are signed, as Protect main
requires. A run of `ci` started by hand (`workflow_dispatch`) would not count
for the required check, which takes only the runs of a pull request or push
event. Only a GitHub App or personal token could skip the approval.

The workflow runs on `pull_request_target` when Dependabot opens, updates or
reopens a pull request into main from a branch of this repo that starts with
`dependabot/npm_and_yarn/docker/bitwarden-cli/`. That event runs main's copy
of the workflow with a token that can write, so the workflow runs nothing from
the pull request. It is safe for these reasons:

- It reads the pull request again through the API and stops unless it is open
  and goes into main from a branch of this repo with that prefix whose author
  is `dependabot[bot]`. A run by hand takes the number of the pull request and
  makes the same check.
- It checks out main into `main/` and the pull request's head into `head/`,
  both without credentials. It runs only main's `scripts/update-deps` with
  `--root head`, which reads and writes a fixed list of data files there
  (`docs/releases.md` among them).
  `tests/repo/test_dependabot_cli_pin_workflow.py` checks that no step runs a
  file of `head/`.
- The version must be plain `X.Y.Z` before it reaches a git ref, a doc or the
  subject, and the number of the pull request plain digits before it reaches
  the release notes. The script asks only `https://github.com/bitwarden/clients`
  and refuses a file that is a symbolic link.
- Its token has only `contents: write` for the commit and
  `pull-requests: read`. The workflow uses no secret.
- It sends the files that the script changed to the API (GraphQL
  `createCommitOnBranch`), and GitHub signs the commit it makes there. A commit
  made with `GITHUB_TOKEN` starts no run of this workflow again.

A pull request that pins the source already gets no commit. When Dependabot
rebases its pull request (`@dependabot rebase`) the commit goes away and the
workflow adds it again. Run it by hand from the workflow's page ("Run
workflow" with the pull request's number), or write the pin in a checkout of
the branch:

```sh
scripts/update-deps --cli-pin             # this checkout
scripts/update-deps --cli-pin --root DIR  # another checkout, DIR
```

Check a change to the workflow with actionlint, as for the docs workflow below.

## The version

The version (docs/spec.md has the rules) is written by hand in one place,
`backend/pyproject.toml`, in Python's spelling (`1.0.0b1`).
`frontend/package.json` repeats it in semver's spelling (`1.0.0-beta.1`), and
the lock files carry generated copies: the package `bwtidy` in
`backend/uv.lock` in Python's spelling, the top-level `version` and
`packages[""].version` of `frontend/package-lock.json` in semver's.
`scripts/ci` fails when one of them differs from `backend/pyproject.toml`, and
names the file and both versions. After a change to the version, `uv lock` in
`backend/` and `demo/` (its lock holds the version of `bwtidy` too) and
`npm install --package-lock-only` in `frontend/` write the copies in the lock
files. `scripts/release prepare` does all of it for a release
([To release](#to-release)).

bwtidy is in beta: feature complete for 1.0, with a stored format (the
database, the bwtidy note, the backups, the settings) changing only with a
migration. A new beta (`1.0.0-beta.N`) comes whenever something is fixed or
the Bitwarden CLI has a new release. The beta becomes a release candidate
(`1.0.0-rc.N`) when the gates of
[Road to 1.0](../docs/explanation/road-to-1.0.md) hold, and the release
candidate becomes 1.0.0 after 8 weeks with no serious bug.

## The release notes

`docs/releases.md` is the release notes page of the docs site, and the workflow
"Release" writes each version's entry into the body of its GitHub release
([To release](#to-release)). It is written for the
people who run bwtidy, in one format that `scripts/ci` checks (the check
"versions"). The rules below hold for anyone who changes bwtidy, a person or an
AI agent.

When your pull request changes something a user sees, write its bullet under
`## Unreleased` in the same pull request. CI checks that it does
([The release note of a pull request](#the-release-note-of-a-pull-request)).
`scripts/release prepare` turns Unreleased into the version's entry when
releasing ([To release](#to-release)).

The page:

- The file is `docs/releases.md`, a page of the docs site. There is no
  `CHANGELOG.md` at the repo root.
- It opens with `# Release notes` and two sentences. The first says that the
  versions follow Semantic Versioning with `-alpha.N`, `-beta.N` and `-rc.N`
  pre-releases. The second links to Road to 1.0, which lives in
  `docs/explanation/road-to-1.0.md`.
- `## Unreleased` is always the first `##` heading. Its first line is the
  sentence "Changes on main that are not yet in a versioned Docker image."
  Then come either bullets (with the same `###` headings a version may have)
  or the line "Nothing yet." alone.
- Each version has a bare heading `## VERSION`, the version as
  `frontend/package.json` spells it (`## 1.0.0-beta.1`), newest first. Each
  version is there once, and no other `##` heading is used.

The metadata line of a version comes right below its heading:

```markdown
YYYY-MM-DD · [GitHub release](https://github.com/tnek213/bwtidy/releases/tag/vVERSION) · [All changes](https://github.com/tnek213/bwtidy/compare/vPREVIOUS...vVERSION)
```

- PREVIOUS is the next older version heading of the file. The oldest version
  has no All changes part.
- The date is the UTC date of the commit that the tag `vVERSION` points at:
  `TZ=UTC git log -1 --format=%cd --date=format-local:%Y-%m-%d 'vVERSION^{commit}'`.
- The line may wrap like any other text (a line break before a `·`), as text
  is wrapped at 80 characters.
- The newest version may have no metadata line while its tag does not exist
  yet (prepared, not released). Every version below it has a tag.

The entry of a version:

- An optional short paragraph comes first, then the bullets.
- Ordinary changes are one flat list, with no nested bullets. An entry with a
  warning heading orders its headings `### Breaking changes`, `### Security`
  and `### Deprecated` (only those with bullets), then puts the flat list
  under `### Other changes`. Without a warning heading there is no
  `### Other changes` and no other `###` heading. No other heading level is
  used inside an entry.
- Breaking changes: anything that can stop an existing setup after an upgrade,
  such as a stored format that needs a migration, a setting that is now refused
  or a removed option. The bullet says what to do.
- Security: only a fixed vulnerability or exposure, where readers should
  update. A new protection (a scan, a provenance attestation, hardening) is an
  ordinary bullet.
- Deprecated: something that still works but will go away.

The bullets:

- Scope: a bullet covers a change to how people use, run, secure or recover
  bwtidy. Routine docs polish, CI, tests, refactors and contributor process
  are left out. A new tutorial or how-to page that lets people do something
  new counts.
- Voice: tell the reader what they can now do, action first ("Use the web UI
  with a screen reader or a keyboard."). A fix or a removal says what changed
  for them ("Find archived items and their attachments in every backup
  again."). A Breaking bullet starts with what to do. Use plain words and no
  internal names unless the reader types them.
- Every bullet ends with its pull request links in one pair of parentheses,
  several separated by a comma and a space:
  `([#23](https://github.com/tnek213/bwtidy/pull/23), [#35](https://github.com/tnek213/bwtidy/pull/35))`.
  Every change reaches main through a pull request, so every bullet has at
  least one.
- Links to other docs pages stay relative, as the page is on the docs site.
  The GitHub release body gets them as absolute URLs.

An entry with a breaking change:

```markdown
## 1.0.0-beta.3

2026-12-01 · [GitHub release](https://github.com/tnek213/bwtidy/releases/tag/v1.0.0-beta.3) · [All changes](https://github.com/tnek213/bwtidy/compare/v1.0.0-beta.2...v1.0.0-beta.3)

### Breaking changes

- Set `BWTIDY_EXAMPLE` before you start the new image, as bwtidy now refuses
  to start without it ([#41](https://github.com/tnek213/bwtidy/pull/41))

### Other changes

- Use the web UI with a screen reader or a keyboard
  ([#23](https://github.com/tnek213/bwtidy/pull/23), [#35](https://github.com/tnek213/bwtidy/pull/35))
- Find archived items and their attachments in every backup again
  ([#40](https://github.com/tnek213/bwtidy/pull/40))
```

`scripts/ci` reports every rule the page breaks, each as
`docs/releases.md:LINE: what is wrong`. It checks the `#` heading,
Unreleased with its sentence, the version headings with their order, the
current version (`frontend/package.json`), the metadata lines and their
links, the `###` headings and the pull request links that end each bullet.
It compares each date with the tags `v*` of the checkout, so it needs them:
in a clone without tags, `git fetch --tags` first.

## To release

A release takes a pull request that prepares the version, its merge and a tag.
The workflow "Release" (`.github/workflows/release.yml`) does the rest:

1. On a branch off main, run `scripts/release prepare VERSION`, with the
   version as `frontend/package.json` spells it (`1.0.0-beta.3`). It renames
   `## Unreleased` to `## VERSION` with its metadata line, dated today (UTC)
   and linking the version below, and puts a new `## Unreleased` with
   "Nothing yet." above it. It bumps the version in `backend/pyproject.toml`
   and `frontend/package.json` and refreshes the lock files as
   [The version](#the-version) says. Then it runs the check of the release
   notes and prints the files to commit.
2. Commit them as "Release bwtidy VERSION" in a pull request and merge it the
   same day (UTC), as the tag must have the date of the entry.
3. Run `scripts/release tag VERSION`. It fetches origin and refuses unless
   main's version is VERSION, its entry is dated the UTC date of main's last
   commit and the tag `vVERSION` does not exist. Then it tags that commit with
   the annotated tag `vVERSION` and pushes the tag.

When the dates differ, the merge came a day later. Run
`scripts/release prepare VERSION` again in a new pull request, which writes
today's date into the entry of a version without a tag, and tag after its
merge.

`prepare` refuses a new version while Unreleased says "Nothing yet.", as the
version would name no change. A version that is the current one already and
has no tag (written by hand, or prepared on an earlier day) keeps its entry
and the version files. The bullets of Unreleased join the entry, under its
`###` headings where it has them, and its metadata line gets today's date in
place of an older one.

### The workflow Release

On a push of a tag `v*` its job `release` checks out the tag with the whole
history and its tags. It runs the check of the release notes
(`scripts/release lint`, the check "versions" of `scripts/ci`) and checks the
tag as `scripts/release tag` does (`scripts/release check-tag VERSION`). Then
it creates the GitHub release `vVERSION` titled "bwtidy VERSION" with
`gh release create --verify-tag`, as a pre-release when the version has a
pre-release suffix. Its body is what `scripts/release notes VERSION` prints:
the version's entry without its heading and metadata line, every relative
link made absolute on the docs site (<https://tnek213.github.io/bwtidy/>,
which serves a page `X.md` at `X/`). Run it to see the body before you tag. A
run again after a failure finds the release and writes its body instead.

Then its job `image` calls the workflow "Image" with the version, which pushes
the image as `VERSION` and `latest`
([Publishing the image](#publishing-the-image)). GitHub starts no workflow
from an event that a workflow's `GITHUB_TOKEN` causes, so "Image" would never
see the release published, and "Release" calls it instead. Only the job
`release` and the job `sync` below can write to the repository's contents,
`image` has the permissions of the provenance attestation.

On every push to main that changes `docs/releases.md` or the workflow, and by
hand ("Run workflow"), its job `sync` writes the notes of each version of
`docs/releases.md` that has a GitHub release into its body where they differ.
So a correction goes into `docs/releases.md` alone, and its merge brings it to
GitHub.

Check a change to the workflow with actionlint, as for the docs workflow below.

### The release note of a pull request

The job `ci` of `.github/workflows/ci.yml` checks on every pull request that a
change a user sees has its bullet, with `scripts/release check-pr` and the
pull request's base, head and labels. It fails when the pull request changes
a path of `USER_VISIBLE` in `scripts/devlib/release.py` but leaves
`## Unreleased` of `docs/releases.md` as it was where the branch left main.
Those paths are `backend/` without its tests, `frontend/` without its tests,
`docker/` without the Docker Hub overview, `Dockerfile`, `compose.yaml` and
`example.env`. The label `no release note` passes such a pull request, for a
change that no user notices (a refactor, a routine dependency bump). The
check runs after `scripts/ci` and whatever it found, and the workflow runs
again when a label is added or removed (the `pull_request` types `labeled` and
`unlabeled`).

## The documentation site

```sh
scripts/docs-site serve           # preview on http://localhost:8000, rebuilt on every change
scripts/docs-site build [DIR]     # the strict build, into DIR (missing, empty or a build) or a temp directory
```

The pages of `docs/` are also a Zensical site (`zensical.toml` at the repo
root), which the image builds and the gateway serves at `/docs/`. Zensical
is pinned in the backend's dependency group `docs`, and `scripts/docs-site`
runs it with `uv run --locked --only-group docs`. `serve` takes Zensical's options
after it (`--dev-addr localhost:8001` when the port is taken) and leaves
`site/` and `.cache/` in the checkout, both git-ignored.

The build puts the site's version into its copy of `docs/restore.md`, on the
line `Written for bwtidy COMMIT.`, as every backup does in its `RESTORE.md`:
`backend/pyproject.toml`'s version as semver spells it, then the commit from
`BWTIDY_COMMIT` (the Dockerfile's docs stage takes it as a build arg) or else
the checkout's last commit. The page in the checkout keeps `COMMIT`.

A page offers a shell command for Bash and for Windows PowerShell as a pair in
plain Markdown (AGENTS.md, Writing docs): the paragraph
`Linux, macOS, WSL (Bash):` and a `sh` code block, then the paragraph
`Windows (PowerShell):` and a `powershell` code block. After the version is
in, the build turns each pair of its copy into two content tabs titled
"Linux, macOS, WSL (Bash)" and "Windows (PowerShell)", each code block indented
below its title. `zensical.toml` links the tabs (`content.tabs.link`), so a
click on one tab shows that shell in every tab group of the page and the site
keeps the choice. A label not directly followed by its code block, or one half
of a pair without the other, fails the build with the page and the line. The
page in the checkout keeps the plain pairs, which GitHub, a backup's
`RESTORE.md` and `serve` show as they are.

### Diagrams

A page holds a diagram as a fenced code block whose language is a Kroki
diagram type (`KROKI_TYPES` in `scripts/docs-site`, every type
<https://kroki.io/> lists) with `alt="TEXT"` after it. Before Zensical runs,
the build turns each such block of its copy of `docs/` into a PNG beside the
page and a Markdown picture of it with that alt text. It sends the source to
the Kroki server that `BWTIDY_KROKI_URL` names, <https://kroki.io> (the free
public one) when it is unset, and asks for SVG, as 12 of Kroki's types have
no PNG and its PNGs are drawn at 1x. resvg (`resvg-py` in the group `docs`)
converts the SVG at twice its size on a white background, and the picture is
shown at half its width, so it is sharp on a HiDPI screen. A transparent
picture would vanish in the dark color scheme. A Mermaid source gets the
first line `%%{init: {"htmlLabels": false, ...}}%%` unless it starts with an
init directive of its own, as resvg leaves out the HTML labels Mermaid writes
otherwise. resvg draws the text with the system's fonts, so the Dockerfile's
docs stage installs `fonts-dejavu-core` (in that stage only). For serif,
sans-serif and monospace it takes the first installed font of the lists in
`GENERIC_FONTS` of the script, as resvg's own choices (Times New Roman, Arial,
Courier New) are missing on many Linux systems and resvg leaves out a label
whose font it cannot find.

A label in a font that is not installed would be drawn in the serif font, so
before resvg runs the build gives each font-family list without a generic
family the one that `NAMED_FONTS` of the script lists for its font.
Helvetica, Arial and their kin get sans-serif. Times and Georgia get serif,
Courier and Consolas monospace. A font that d2 embeds in its SVG (resvg loads
no embedded font) gets sans-serif, or monospace for its code font, with the
bold or italic its name holds. Graphviz writes `sans-Serif` after Helvetica,
and the build writes such a generic family in lower case (the only case resvg
knows). Name a generic family after any other font, as in
`fontname="Inter, sans-serif"`. Where that font is not installed, a list
without one fails the build, which names the page, the line and the font.

Building docs with a diagram therefore needs one of those fonts for each
generic family its text uses, and DejaVu covers all three. Without one the
build fails before it writes the picture and names the page, the line, the
family and the fonts it tried. The docs stage and the workflows `ci.yml` and
`docs.yml` install `fonts-dejavu-core` with apt-get, which is also what you
install on Debian or Ubuntu. Fedora has `dejavu-sans-fonts`,
`dejavu-serif-fonts` and `dejavu-sans-mono-fonts`, Alpine `font-dejavu` and
Arch `ttf-dejavu`. macOS and Windows have Times New Roman, Arial and Courier
New already.

A Graphviz or PlantUML picture sized in `pt` is drawn at 96 dpi, the CSS
pixel's.

Each PNG is kept in `.cache/kroki/` at the repo root (git-ignored), named
after a hash of the type, the source and the converter's settings, and a
later build takes it from there without asking the server. So building docs
that hold a diagram needs a Kroki server, unless the cache has every one
(the image's build has no cache). The converter writes into a temp directory
inside that cache, so a temp directory on another filesystem (such as a tmpfs
`/tmp`) does not matter. A server that cannot be reached or answers
with an error fails the build, naming the page, the line and the server's
message. A diagram without alt text fails it before any request, and
`scripts/docs-lint` reports it already. Docs without a diagram build without
any request. To use a Kroki server of your own, set `BWTIDY_KROKI_URL`: the
Dockerfile's docs stage takes it as a build arg, and `scripts/dev-vault up`,
`compose.dev.yaml` and `compose.build.yaml` pass on the one of the
environment. `serve` shows a diagram as its code block, an editor's Kroki
preview or a build shows the picture. That takes `relaxed_headers` of
SuperFences in `zensical.toml`: SuperFences refuses a fence whose info string
holds an option it does not know (such as `alt`) and leaves it as text, so
its closing fence would open a code block of what follows.

The site's colors and its code font are in
`docs/assets/stylesheets/theme.css` (`extra_css` in `zensical.toml`): a warm
ivory and deep teal palette for each of the theme's two color schemes
(`default` and `slate`, whose palettes name the colors `custom`, so none of
the theme's named colors applies), the colors of the banner and the boxes,
and IBM Plex Mono for code. The font's regular and bold woff2 files, IBM's
complete build with the box-drawing characters of the directory trees, come
from the npm package `@ibm/plex-mono` (the files only, as its postinstall
would report to IBM's telemetry) and lie in `docs/assets/fonts/ibm-plex-mono/`
with its `LICENSE.txt` (SIL Open Font License 1.1). The package's entry in
`BUNDLED` of `scripts/third-party` puts it on the About page's list: files
of another version change the version there too. The search dialog draws
in a shadow root of its own: the stylesheet colors it through the theme's
`--color-*` variables, but its code stays in the browser's monospace font.

The theme overrides are in `overrides/` (outside `docs/`, so they are not
built as pages). `partials/copyright.html` is the line at the bottom of the
footer, which credits Zensical and the Diátaxis framework. `main.html` fills the
theme's `announce` block with the beta disclaimer banner on every page, one slim
line with its text centered, which links to the disclaimer at the top of
`docs/index.md`. Its inline style widens the content column (`.md-grid`, the
theme's 61rem leaves tables little room), keeps paragraphs, list items, quotes
and admonitions as well as headings at most 72 characters wide (`72ch`, in each
one's own font) unless they hold a picture, table or code block, keeps inline
code on one line (in text and tables, except on a narrow screen where it would
not fit) and frames the pictures in the content (a 1 px border and a small
shadow, so a white screenshot shows on a white page), and its inline script
makes a picture shown smaller than it is open larger in a dialog on a click
(`data-zoom`). It also links every page to the issue form
`.github/ISSUE_TEMPLATE/docs_improvement.yml` (`partials/improve-link.html`
builds the link, `partials/toc.html` puts it in the right sidebar below the
table of contents and `main.html` at the end of the content on a narrow screen),
with the page's source path, its address on the GitHub Pages site and the
version, which `scripts/docs-site` hands over as `extra.bwtidy_version`. Both
stay inline, as a running bwtidy serves the site offline: nothing comes from a
CDN. Zensical's own lightbox (its glightbox extension) loads GLightbox from
unpkg.com, so it stays off. For the same reason the Markdown extensions in
`zensical.toml` are those whose output needs nothing from another host: GitHub's
alerts as boxes (`pymdownx.quotes` with `callouts`), the cards (`md_in_html` and
`attr_list`) and their icons, which `pymdownx.emoji` draws inline from the
theme's icon sets. An emoji shortcode such as `:smile:` would be a picture from
a CDN, and a Mermaid diagram that Zensical draws loads its script from one, so
diagrams become pictures at build time (see Diagrams above). After the build,
`scripts/docs-site build` checks
that every built HTML page has the banner (the attribute `data-disclaimer`)
and fails naming those without it. It also fails on a numbered list that
starts at 1 again right after another one with only a picture or code block
between them, naming the page and the line of its HTML: a step's picture, code
block or paragraph is indented by 4 spaces under it, or the list ends there.
And it fails on a page that has the browser fetch anything from another host
(a script, stylesheet, icon, picture, frame or CSS `url()`), naming the page,
the line and the URL. A link to another host is fine.

The docs fetch nothing from another site and send it nothing about the host
they are served from, as the address of a self-hosted bwtidy stays private.
The theme's repository link in the header (`partials/source.html`) would have
its script ask `api.github.com` for the latest release and the star count on
every page, so the override drops the attribute that starts it
(`data-md-component="source"`) and writes the facts at build time instead: the
version the site is built for (`extra.bwtidy_release`, the semver alone, so a
self-hosted bwtidy shows the version it runs) and the star count
(`extra.bwtidy_stars`), which `scripts/docs-site` takes from the environment
variable `BWTIDY_STARS` when it holds only digits. The workflows "Docs" and
"Image" look the count up with `gh api` before the build (the image passes it
as the build arg `BWTIDY_STARS`), a failed lookup only leaves it out, and a
build without it shows only the version. `main.html` also sets the referrer
policy `same-origin` and the issue link has `rel="noopener noreferrer"`, so a
click on a link to another site sends no `Referer`.

`scripts/ci` runs `scripts/docs-site build` after the MCP reference check.
It fails on a page of `docs/` that the nav of `zensical.toml` leaves out,
and Zensical's strict mode on a nav entry, page link or `#anchor` that does
not resolve in the site. A link from `docs/` to a file outside it (such as
this guide) resolves in the repo but not in the site, so a page names such a
file as code. The dev stack serves the site its image was built with: after
a change to the docs, `scripts/dev-vault up` rebuilds it, or preview with
`serve`.

`build DIR` puts the new site next to the old one first, renames the old one
to `.old-NAME`, renames the new one into place and then removes the old one,
so DIR is never without a site, and the old one is renamed back when the second
rename fails.

### GitHub Pages

The workflow "Docs" (`.github/workflows/docs.yml`) publishes the site at
<https://tnek213.github.io/bwtidy/>. It runs on every push to `main` and by
hand (`workflow_dispatch`, "Run workflow" on the workflow's page). Its build
job runs `scripts/docs-site build _site` and uploads `_site` (git-ignored) as
the Pages artifact, its deploy job deploys it with `actions/deploy-pages`.
Both jobs skip while the repo is private (GitHub's free plan serves Pages
only from public repos), so a push then shows them as skipped, not failed.
The repo's Pages settings need "GitHub Actions" as the source.

The site has no `site_url`, so its pages link to each other relatively and
the same build works at `/bwtidy/` on Pages and at `/docs/` in the app. To
see what Pages will serve, build it and serve the folder, then open
<http://localhost:8000/>:

```sh
scripts/docs-site build _site
python3 -m http.server -d _site 8000
```

`scripts/docs-site serve` shows the same pages and rebuilds on every change.
Check a change to the workflow with actionlint:

```sh
docker run --rm -v "$PWD:/repo" -w /repo rhysd/actionlint:1.7.12
```

## Publishing the image

The workflow "Image" (`.github/workflows/image.yml`) builds the Dockerfile's target
`app` and pushes it to `docker.io/tnek213/bwtidy` on Docker Hub. It runs on every
push to main, for every release (pre-releases too), when the workflow "Release"
calls it with the version (`workflow_call`), and by hand on main. The image is
built for `linux/amd64` with the OCI labels `source`, `version`, `revision` and
`licenses`. The tags depend on the trigger:

| Trigger | Tags |
| --- | --- |
| Push to main | `main` and the short commit (the first 7 characters of its hash) |
| Release (a call from "Release") | the version as `frontend/package.json` spells it (`1.0.0-beta.1`) and `latest` |

The image builds on Debian 13 (trixie): `python:3.13-slim-trixie` is the base of
the final stage, `node:24-trixie-slim` of the build stages. The stages
`frontend`, `docs`, `bitwarden-cli` (`npm ci` in `docker/bitwarden-cli/`) and
`backend` (the venv) build what the final stage `app` copies: the node binary,
the CLI's `node_modules`, the venv, the static files and the docs site. The
stage `bitwarden-cli-source` builds the CLI itself, Bitwarden's open source
build, from the commit of `bitwarden/clients` that
`docker/bitwarden-cli/source.json` pins (`npm ci` and `npm run build:oss:prod`
in `apps/cli`). It fails when the tag no longer names that commit or when the
build bundles a module under `bitwarden_license/`, and `bitwarden-cli` puts its
`apps/cli/build` in place of the npm package's build, with a `SOURCE` file that
names the repository, the tag and the commit and the GPL-3.0 text beside it.
The build also bundles Bitwarden's SDK `@bitwarden/sdk-internal` (GPL-3.0),
whose npm package holds only its compiled WebAssembly. So `SOURCE` names the
SDK's repository and the commit from the package's `VERSION` too, and the
folder `sdk-internal` beside it keeps the package's `package.json`, `VERSION`
and `LICENSE` with the sha256 of its WebAssembly. The stage fails when
`VERSION` is not a commit or when the build holds a WebAssembly file that is
not the SDK's, as `SOURCE` would not name its source.
npm, git, uv, uv's cache, the monorepo and the backend's sources stay in the
build stages. The app stage
also removes pip, which comes with the base image: nothing in the image runs it,
and a scanner would report on its vendored packages.

A push to main that changes only files the image never copies (the
`paths-ignore` list in the workflow: `README.md`, the other top-level docs, the
Docker Hub overview, the compose files, `dev/`, the tests, the other workflows,
`scripts/prune-tags`, `scripts/release`) builds nothing. Keep that list in step
with the Dockerfile's `COPY` lines: a file the image copies must never be on it.
A push to main never moves `latest`. The workflow fails, saying why, when the
version of a release or its tag is not the version in `frontend/package.json`
(or `v` plus it), when it is run by hand on another branch, or when the
repository secret `DOCKERHUB_TOKEN` is missing. The job skips while the
repository is private.

Set it up once:

- Create the repository `tnek213/bwtidy` on Docker Hub first. Docker Hub creates
  a repository that does not exist yet as public when you push to it.
- Make a Docker Hub personal access token with Read and Write and store it as the
  repository secret: `gh secret set DOCKERHUB_TOKEN --repo tnek213/bwtidy`.

A release pushes `VERSION` and `latest` once its tag is pushed
([To release](#to-release)).

Every pushed image gets a signed SLSA build provenance attestation
(`actions/attest-build-provenance`), stored on GitHub and pushed next to the
image, plus BuildKit's own provenance and SBOM. Anyone can check that an image
was built by this workflow from a given commit of this repository:

```sh
gh attestation verify oci://docker.io/tnek213/bwtidy:latest --owner tnek213
```

Check a change to the workflow with actionlint, as for the docs workflow above.

## Scanning the published images

The workflow "Image scan" (`.github/workflows/image-scan.yml`) scans
`docker.io/tnek213/bwtidy:latest` and `:main` with Docker Scout every day at
04:17 UTC, after every successful run of "Image" on main, on a pull request that
changes the workflow and by hand ("Run workflow"). It uploads the results as
SARIF to code scanning, so the alerts show under the repository's Security tab,
"Code scanning", one category per tag (`image-latest` and `image-main`). It
reports only the vulnerabilities that a newer version of the package fixes
(`only-fixed`): those are the ones a rebuild or an update can act on, and an
alert closes when a scan no longer finds it. The data is the same as the Scout
view on Docker Hub, which also lists the unfixed ones. The job skips while the
repository is private.

Scout needs a Docker Hub login even for a public image, so the workflow has a
secret of its own that can only read. Set it up once: make a Docker Hub personal
access token with the description `bwtidy-scout-read` and the scope Public Repo
Read-only, and store it with
`gh secret set DOCKERHUB_SCOUT_TOKEN --repo tnek213/bwtidy`. The workflow fails,
saying why, when the secret is missing. A pull request from Dependabot or a fork
gets no repository secrets, so its scan fails and the next scheduled run checks
the change.

Check a change to the workflow with actionlint, as for the docs workflow above.

## Pruning the short-commit tags

Every push to main that changes the image adds a short-commit tag, so the workflow
"Prune image tags" (`.github/workflows/prune-tags.yml`) deletes the old ones with
`scripts/prune-tags` every Sunday at 03:41 UTC. The script lists every tag of the
repository through Docker Hub's API and keeps:

- every tag that is not exactly 7 lowercase hex characters: the versions, `latest`,
  `main` and any other name. The version tags are immutable on Docker Hub anyway
  (a repository setting with the regex `^[0-9]+\.[0-9]+\.[0-9]+(-.+)?$`).
- the 10 newest short-commit tags by last push, so a recent build can still be run
  or compared.
- the short commit of every commit a git tag `v*` points to, as a server may run
  a release by its commit tag. The workflow checks out the full history with its
  tags, and the script stops without deleting anything when Docker Hub has a
  version tag but the checkout has no tag `v*`.
- every short-commit tag with the same digest as `main` or `latest`.

It deletes the rest, prints why it keeps each tag it keeps, and stops with an error,
deleting nothing, when the listing fails or finds no tags. A failed delete is
reported, the others go on and the run fails.

The workflow also runs by hand ("Run workflow"), as a dry run unless `dry_run` is
unticked, and as a dry run on every pull request that changes the workflow or the
script. A dry run prints what it would delete and deletes nothing. Run one by hand
from a checkout with its tags (`git fetch --tags`):

```sh
scripts/prune-tags              # a dry run, anonymous as the repository is public
scripts/prune-tags --delete     # deletes, needs the token in DOCKERHUB_PAT
```

With `DOCKERHUB_PAT` set, the dry run logs in too, which checks the token. Deleting
a tag needs a personal access token with Read, Write, Delete, so the workflow uses
the secret `DOCKERHUB_README_TOKEN` of the Docker Hub overview below. The script
logs in at `/v2/auth/token` (`/v2/users/login` is deprecated) and never prints
the token. A pull request from Dependabot or a fork gets no repository secrets,
so its dry run fails and the next scheduled run checks the change. Check a
change to the workflow with actionlint, as for the docs workflow above.

## The Docker Hub overview

The workflow "Docker Hub README" (`.github/workflows/docker-readme.yml`) copies
`docker/hub-overview.md` to the repository's overview on Docker Hub and the GitHub
repository's description to its short description. It runs on every push to main
that changes the overview or the workflow, and by hand. Docker Hub takes at most
25,000 bytes of overview and 100 of short description.

The overview is a file of its own and follows the overviews of Docker Official Images
such as `httpd` and `mariadb`. A reader on Docker Hub looks for the tags and how
to run the image first. `README.md` also leads a reader on GitHub to the
contributing files and the developer guide. The overview starts with a quick
reference and the tags. The introduction and the caution follow, then how to run
the image with its port and volumes and its main settings. The license comes
last. For everything else it links to the docs site.

Docker Hub draws Markdown its own way. It hides HTML comments. Tables, code
blocks and plain quotes look as they do on GitHub. It shows a GitHub alert
(`> [!WARNING]`) as a quote titled Warning but prints the Markdown inside as
plain text, so the overview holds the caution as a plain quote. It resolves no
relative link. Every link in the overview is therefore absolute and the workflow
needs no URL completion.

The overview shares the introduction, the caution and the trademark note with
`README.md`. AGENTS.md (Writing docs) says which texts must stay the same, and
`backend/tests/repo/test_docker_hub_overview.py` checks them. It also checks the
links, the size and that every variable the overview names is in the
configuration reference. The workflow "Image" ignores a push that changes only
the overview.

The description API refuses a token with only Read and Write, so this workflow
has a secret of its own, which it shares with the pruning of the short-commit
tags ([Pruning the short-commit tags](#pruning-the-short-commit-tags)), as
deleting a tag needs the same scope: make a Docker Hub personal access token
with Read, Write, Delete and store it with
`gh secret set DOCKERHUB_README_TOKEN --repo tnek213/bwtidy`. Both workflows
fail, saying why, when the secret is missing. `DOCKERHUB_TOKEN` (Read and Write)
stays the one the image is pushed with.

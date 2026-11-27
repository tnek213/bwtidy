# Releasing

The version and its release notes, the documentation site and its publishing
on GitHub Pages, and publishing the image.

## The version

The version (docs/spec.md has the rules) is written by hand in one place,
`backend/pyproject.toml`, in Python's spelling (`1.0.0b1`).
`frontend/package.json` repeats it in semver's spelling (`1.0.0-beta.1`), and
the lock files carry generated copies: the package `bwtidy` in
`backend/uv.lock` in Python's spelling, the top-level `version` and
`packages[""].version` of `frontend/package-lock.json` in semver's.
`scripts/ci` fails when one of them differs from `backend/pyproject.toml`, and
names the file and both versions. After a change to the version, `uv lock` in
`backend/` and `demo/` (its lock holds the version of `bwtidy` too) and `npm install --package-lock-only` in `frontend/` write the
copies in the lock files.

bwtidy is in beta: feature complete for 1.0, with a stored format (the
database, the bwtidy note, the backups, the settings) changing only with a
migration. A new beta (`1.0.0-beta.N`) comes whenever something is fixed or
the Bitwarden CLI has a new release. The beta becomes a release candidate
(`1.0.0-rc.N`) when the gates of Road to 1.0 in `docs/releases.md` hold, and
the release candidate becomes 1.0.0 after 8 weeks with no serious bug.

## The release notes

`docs/releases.md` holds a few lines for each version, newest first, written
for users: what the version brings, under a heading that starts with the
version as `frontend/package.json` spells it (`## 1.0.0-beta.1`). `scripts/ci`
fails while it has no heading for the current version, so the entry goes into
the commit that changes the version.

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

### Diagrams

A page holds a diagram as a fenced code block whose language is a Kroki
diagram type (`KROKI_TYPES` in `scripts/docs-site`, every type
https://kroki.io/ lists) with `alt="TEXT"` after it. Before Zensical runs,
the build turns each such block of its copy of `docs/` into a PNG beside the
page and a Markdown picture of it with that alt text. It sends the source to
the Kroki server that `BWTIDY_KROKI_URL` names, https://kroki.io (the free
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
preview or a build shows the picture.

The theme overrides are in `overrides/` (outside `docs/`, so they are not
built as pages). Its `main.html` fills the theme's `announce` block with the
beta disclaimer banner on every page, one slim line with its text centered,
which links to the disclaimer at the top of `docs/index.md`. Its inline style widens the content column (`.md-grid`,
the theme's 61rem leaves tables little room), keeps paragraphs, list items,
quotes and admonitions at most 80 characters wide unless they hold a picture,
table or code block, keeps inline code on one line (in text and tables, except
on a narrow screen where it would not fit) and frames the pictures in the
content (a 1 px border and a small shadow, so a white screenshot shows on a
white page), and its inline script makes a picture shown smaller than it is
open larger in a dialog on a click (`data-zoom`). It also links every page to the
issue form `.github/ISSUE_TEMPLATE/docs_improvement.yml` (`partials/improve-link.html`
builds the link, `partials/toc.html` puts it in the right sidebar below the table of
contents and `main.html` at the end of the content on a narrow screen), with the
page's source path, its address on the GitHub Pages site and the version, which
`scripts/docs-site` hands over as `extra.bwtidy_version`. Both stay inline, as a
running bwtidy serves the site offline: nothing comes from a CDN. Zensical's
own lightbox (its glightbox extension) loads GLightbox from unpkg.com, so it
stays off. For the same reason the Markdown extensions in `zensical.toml` are
those whose output needs nothing from another host: GitHub's alerts as boxes
(`pymdownx.quotes` with `callouts`), the cards (`md_in_html` and `attr_list`)
and their icons, which `pymdownx.emoji` draws inline from the theme's icon
sets. An emoji shortcode such as `:smile:` would be a picture from a CDN, and
a Mermaid diagram that Zensical draws loads its script from one, so diagrams
become pictures at build time (see Diagrams above). After the build,
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
https://tnek213.github.io/bwtidy/. It runs on every push to `main` and by
hand (`workflow_dispatch`, "Run workflow" on the workflow's page). Its build
job runs `scripts/docs-site build _site` and uploads `_site` (git-ignored) as
the Pages artifact, its deploy job deploys it with `actions/deploy-pages`.
Both jobs skip while the repo is private (GitHub's free plan serves Pages
only from public repos), so a push then shows them as skipped, not failed.
The repo's Pages settings need "GitHub Actions" as the source.

The site has no `site_url`, so its pages link to each other relatively and
the same build works at `/bwtidy/` on Pages and at `/docs/` in the app. To
see what Pages will serve, build it and serve the folder, then open
http://localhost:8000/:

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
push to main, on every published GitHub release (pre-releases too) and by hand on
main. The image is built for `linux/amd64` with the OCI labels `source`, `version`,
`revision` and `licenses`. The tags depend on the trigger:

| Trigger | Tags |
| --- | --- |
| Push to main | `main` and the short commit (7 characters) |
| Release | the version as `frontend/package.json` spells it (`1.0.0-beta.1`) and `latest` |

The image builds on Debian 13 (trixie): `python:3.13-slim-trixie` is the base of the
final stage, `node:24-trixie-slim` of the build stages. The stages `frontend`, `docs`,
`bitwarden-cli` (`npm ci` in `docker/bitwarden-cli/`) and `backend` (the venv) build
what the final stage `app` copies: the node binary, the CLI's `node_modules`, the venv,
the static files and the docs site. npm, uv, uv's cache and the backend's sources stay
in the build stages. The app stage also removes pip, which comes with the base image: nothing
in the image runs it, and a scanner would report on its vendored packages.

A push to main that changes only files the image never copies (the `paths-ignore` list in
the workflow: `README.md`, the other top-level docs, the compose files, `dev/`, the tests,
the other workflows, `scripts/prune-tags`) builds nothing. Keep that list in step with the Dockerfile's `COPY`
lines: a file the image copies must never be on it. A push to main never moves `latest`. The workflow fails, saying why, when the
release tag is not `v` plus the version in `frontend/package.json`, when it is run
by hand on another branch, or when the repository secret `DOCKERHUB_TOKEN` is
missing. The job skips while the repository is private.

Set it up once:

- Create the repository `tnek213/bwtidy` on Docker Hub first. Docker Hub creates a
  repository that does not exist yet as public when you push to it.
- Make a Docker Hub personal access token with Read and Write and store it as the
  repository secret: `gh secret set DOCKERHUB_TOKEN --repo tnek213/bwtidy`.

To release:

1. Write the version's entry at the top of `docs/releases.md`
   ([The release notes](#the-release-notes)).
2. Bump the version in `backend/pyproject.toml` and `frontend/package.json`
   and update the lock files as [The version](#the-version) says, in the same
   commit as the entry, then push.
3. Publish a GitHub release with the tag `vVERSION`, its notes the text of
   the version's entry. That pushes `VERSION` and `latest`.

Every pushed image gets a signed SLSA build provenance attestation
(`actions/attest-build-provenance`), stored on GitHub and pushed next to the image, plus
BuildKit's own provenance and SBOM. Anyone can check that an image was built by this
workflow from a given commit of this repository:

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
  `main` and any other name. The version tags are immutable on Docker Hub anyway (a
  repository setting with the regex `^[0-9]+\.[0-9]+\.[0-9]+(-.+)?$`).
- the 10 newest short-commit tags by last push, so a recent build can still be run
  or compared.
- the short commit of every commit a git tag `v*` points to, as a server may run a
  release by its commit tag. The workflow checks out the full history with its tags,
  and the script stops without deleting anything when Docker Hub has a version tag
  but the checkout has no tag `v*`.
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
the secret `DOCKERHUB_README_TOKEN` of the Docker Hub overview below. The script logs
in at `/v2/auth/token` (`/v2/users/login` is deprecated) and never prints the token.
A pull request from Dependabot or a fork gets no repository secrets, so its dry run
fails and the next scheduled run checks the change. Check a change to the workflow
with actionlint, as for the docs workflow above.

## The Docker Hub overview

The workflow "Docker Hub README" (`.github/workflows/docker-readme.yml`) copies
`README.md` to the repository's overview on Docker Hub and the GitHub repository's
description to its short description, so Docker Hub says what GitHub says. It runs on
every push to main that changes `README.md` or the workflow, and by hand. Relative
links and images in the README become links into the GitHub repository. Docker Hub
takes at most 25,000 bytes of README.

The description API refuses a token with only Read and Write, so this workflow has a
secret of its own, which it shares with the pruning of the short-commit tags
([Pruning the short-commit tags](#pruning-the-short-commit-tags)), as deleting a tag
needs the same scope: make a Docker Hub personal access token with Read, Write, Delete
and store it with `gh secret set DOCKERHUB_README_TOKEN --repo tnek213/bwtidy`. Both
workflows fail, saying why, when the secret is missing. `DOCKERHUB_TOKEN` (Read and
Write) stays the one the image is pushed with.

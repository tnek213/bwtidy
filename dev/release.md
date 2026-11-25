# Releasing

The version, the documentation site and its publishing on GitHub Pages, and
publishing the image.

## The version

The version (docs/spec.md has the rules) is written by hand in one place,
`backend/pyproject.toml`, in Python's spelling (`0.1.0a1`).
`frontend/package.json` repeats it in semver's spelling (`0.1.0-alpha.1`), and
the lock files carry generated copies: the package `bwtidy` in
`backend/uv.lock` in Python's spelling, the top-level `version` and
`packages[""].version` of `frontend/package-lock.json` in semver's.
`scripts/ci` fails when one of them differs from `backend/pyproject.toml`, and
names the file and both versions. After a change to the version, `uv lock` in
`backend/` and `npm install --package-lock-only` in `frontend/` write the
copies in the lock files.

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

The theme overrides are in `overrides/` (outside `docs/`, so they are not
built as pages). Its `main.html` fills the theme's `announce` block with the
alpha disclaimer banner on every page, which links to the disclaimer at the
top of `docs/index.md`. After the build, `scripts/docs-site build` checks
that every built HTML page has the banner (the attribute `data-disclaimer`)
and fails naming those without it.

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
| Release | the version as `frontend/package.json` spells it (`0.1.0-alpha.1`) and `latest` |

A push to main that changes only files the image never copies (the `paths-ignore` list in
the workflow: `README.md`, the other top-level docs, the compose files, `dev/`, the tests,
the other workflows) builds nothing. Keep that list in step with the Dockerfile's `COPY`
lines: a file the image copies must never be on it. A push to main never moves `latest`. The workflow fails, saying why, when the
release tag is not `v` plus the version in `frontend/package.json`, when it is run
by hand on another branch, or when the repository secret `DOCKERHUB_TOKEN` is
missing. The job skips while the repository is private.

Set it up once:

- Create the repository `tnek213/bwtidy` on Docker Hub first. Docker Hub creates a
  repository that does not exist yet as public when you push to it.
- Make a Docker Hub personal access token with Read and Write and store it as the
  repository secret: `gh secret set DOCKERHUB_TOKEN --repo tnek213/bwtidy`.

To release, bump the version in `backend/pyproject.toml` and `frontend/package.json`,
update the lock files as [The version](#the-version) says, push, then publish
a GitHub release with the tag `vVERSION`. That pushes `VERSION` and `latest`.

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

## The Docker Hub overview

The workflow "Docker Hub README" (`.github/workflows/docker-readme.yml`) copies
`README.md` to the repository's overview on Docker Hub and the GitHub repository's
description to its short description, so Docker Hub says what GitHub says. It runs on
every push to main that changes `README.md` or the workflow, and by hand. Relative
links and images in the README become links into the GitHub repository. Docker Hub
takes at most 25,000 bytes of README.

The description API refuses a token with only Read and Write, so this workflow has a
secret of its own: make a Docker Hub personal access token with Read, Write, Delete
and store it with `gh secret set DOCKERHUB_README_TOKEN --repo tnek213/bwtidy`. The
workflow fails, saying why, when the secret is missing.

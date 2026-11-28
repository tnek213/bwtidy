# Security policy

bwtidy is beta software made with AI, with very few users so far, and it
changes the contents of a password vault. The full warning is at the top of
[README.md](README.md).

There is no support promise and no fixed response time. Reports are read and
answered when there is time.

## Supported versions

Only the newest release and the `main` branch.

## The Bitwarden CLI

The image runs the open source build of the Bitwarden CLI, built from
Bitwarden's source without changes. A new release of the CLI is followed by a
bwtidy patch release, usually within a few weeks. A vulnerability in the CLI or
in the packages it brings along is Bitwarden's to fix: report it to Bitwarden.

## Verifying the image

Every image on Docker Hub carries a signed build provenance attestation, which shows
that GitHub Actions built it from a given commit of this repository. Check it
with the GitHub CLI:

```sh
gh attestation verify oci://docker.io/tnek213/bwtidy:latest --owner tnek213
```

## Scanning the image

Docker Scout scans the newest release (`latest`) every day, and each known
vulnerability that an update fixes becomes a code scanning alert in this
repository. It is the same data as the Scout view on Docker Hub, which also
lists the vulnerabilities that have no fix yet.

## Reporting a vulnerability

Report it privately through GitHub's private vulnerability reporting:
<https://github.com/tnek213/bwtidy/security/advisories/new>

Never report a vulnerability in a public issue or a pull request.

Never put vault contents, secrets, tokens or backups in a report. Describe
them instead, and say which version you ran (the About page shows it).

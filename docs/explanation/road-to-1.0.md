# Road to 1.0

This page is the plan from the beta to 1.0.

The beta started with 1.0.0-beta.1. bwtidy is feature complete for 1.0, and
the beta is for finding bugs and for feedback from real vaults. From the beta
on, every stored format changes only with a migration. That covers the
database and the bwtidy note as well as the backups and the settings.

```graphviz alt="A new beta comes with each fix and each new release of the Bitwarden CLI. The beta becomes a release candidate once the gates below hold. The release candidate becomes 1.0.0 after 8 weeks with no serious bug, counted from the release candidate."
digraph {
  rankdir=LR
  nodesep=0.4
  ranksep=0.7
  fontname="sans-serif"
  node [fontname="sans-serif" fontsize=13 shape=box style="rounded,filled" fillcolor="#ffffff" color="#5c6bc0" margin="0.14,0.08"]
  edge [fontname="sans-serif" fontsize=12 color="#455a64"]
  beta [label="Beta\n1.0.0-beta.N" fillcolor="#e8eaf6"]
  rc [label="Release candidate\n1.0.0-rc.N"]
  final [label="1.0.0" fillcolor="#f1f8e9" color="#558b2f"]
  beta -> beta [label="  a fix or a new\n  Bitwarden CLI release"]
  beta -> rc [label="the gates\nhold"]
  rc -> final [label="8 weeks with\nno serious bug"]
}
```

A new beta (1.0.0-beta.2, 1.0.0-beta.3 and so on) comes whenever something is
fixed or the Bitwarden CLI has a new release. The beta becomes a release
candidate when all of these hold:

- At least 10 "Works for me" reports from different people in the
  [Works for me discussion](https://github.com/tnek213/bwtidy/discussions/16),
  with Bitwarden and Vaultwarden each at least once.
- 8 weeks of real use with no bug that lost or damaged vault data and no open
  security report. The 8 weeks start at the first "Works for me" report by
  someone other than the author rather than at the release. That way they
  measure use instead of time.
- No serious bug open.
- An upgrade from one beta to the next that goes through a migration and is
  tested.
- Docker Hub pulls of at least 0.01% of the pulls of `vaultwarden/server`,
  measured on the day of the decision.

The release candidate becomes 1.0.0 after another 8 weeks with no serious
bug, counted from the release candidate.

# Contributing

bwtidy grows in the open, and reports, ideas and changes are welcome. Each
card links to the issue form or the file in the repository that says how.

<div class="grid cards" markdown>

-   :lucide-bug:{ .lg .middle } __Bug report__

    ---

    Something in bwtidy does not work as it should. The form asks for what
    it takes to look into it.

    [:lucide-arrow-right: Report a bug](https://github.com/tnek213/bwtidy/issues/new?template=bug_report.yml)

-   :lucide-lightbulb:{ .lg .middle } __Change request__

    ---

    A change or an addition to bwtidy. Discuss a new feature there before
    you write it.

    [:lucide-arrow-right: Request a change](https://github.com/tnek213/bwtidy/issues/new?template=feature_request.yml)

-   :lucide-book-open:{ .lg .middle } __Documentation__

    ---

    Something in these docs that is wrong, unclear or missing. Improve this
    page, on every page, opens the form with the page filled in.

    [:lucide-arrow-right: Suggest a docs improvement](https://github.com/tnek213/bwtidy/issues/new?template=docs_improvement.yml)

-   :lucide-git-pull-request:{ .lg .middle } __Pull request__

    ---

    A fix or an agreed change of your own: where to start, the checks and
    how a commit looks.

    [:lucide-arrow-right: Read CONTRIBUTING.md](https://github.com/tnek213/bwtidy/blob/main/CONTRIBUTING.md)

-   :lucide-shield-alert:{ .lg .middle } __Security issue__

    ---

    A vulnerability, reported privately and never in a public issue.

    [:lucide-arrow-right: Read SECURITY.md](https://github.com/tnek213/bwtidy/blob/main/SECURITY.md#reporting-a-vulnerability)

-   :lucide-heart-handshake:{ .lg .middle } __Code of conduct__

    ---

    How everyone taking part behaves, and where to report what breaks it.

    [:lucide-arrow-right: Read the code of conduct](https://github.com/tnek213/bwtidy/blob/main/CODE_OF_CONDUCT.md)

</div>

## A diagram in these docs

A page of these docs can hold a diagram, written as a fenced code block in
any of the diagram languages that [Kroki](https://kroki.io/) supports
(`mermaid`, `plantuml`, `d2`, `graphviz` and the others), with `alt="..."`
after the language to say what it shows:

````markdown
```mermaid alt="A login reaches the gateway, which starts the account's worker"
flowchart LR
  Login --> Gateway --> Worker
```
````

The docs build turns it into a picture, so the docs that a running bwtidy
serves fetch nothing from elsewhere. A diagram without alt text fails the
build. While you write, a Kroki preview in your editor (such as a Kroki
extension for VS Code) shows the picture. Building docs with a diagram needs
a Kroki server, `dev/release.md` (Diagrams) says which.

The rules and reasons behind the code, its map and the developer guide are
under [For contributors](index.md#for-contributors) on the home page.

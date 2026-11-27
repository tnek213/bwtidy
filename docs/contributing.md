# Contributing

bwtidy grows in the open and welcomes reports, ideas and changes. Each card
below leads to the issue form or the file in the repository that tells you
how.

<div class="grid cards" markdown>

- :lucide-bug:{ .lg .middle } **Bug report**

    ---

    Something in bwtidy does not work as it should. The form asks for your
    version, what you did and what happened instead.

    [:lucide-arrow-right: Report a bug](https://github.com/tnek213/bwtidy/issues/new?template=bug_report.yml)

- :lucide-lightbulb:{ .lg .middle } **Change request**

    ---

    You would like bwtidy to work differently or do something new. Discuss
    a new feature there or under Ideas in the Discussions before you write
    it.

    [:lucide-arrow-right: Request a change](https://github.com/tnek213/bwtidy/issues/new?template=feature_request.yml)

- :lucide-book-open:{ .lg .middle } **Documentation**

    ---

    Something in these docs is wrong, unclear or missing. The link "Suggest
    an improvement to this page" on every page opens the form with the page
    and its version filled in.

    [:lucide-arrow-right: Suggest a docs improvement](https://github.com/tnek213/bwtidy/issues/new?template=docs_improvement.yml)

- :lucide-git-pull-request:{ .lg .middle } **Pull request**

    ---

    A bug fix or a docs fix can go straight to a pull request. CONTRIBUTING.md
    says where to start, which checks to run and how a commit looks.

    [:lucide-arrow-right: Read CONTRIBUTING.md](https://github.com/tnek213/bwtidy/blob/main/CONTRIBUTING.md)

- :lucide-shield-alert:{ .lg .middle } **Security issue**

    ---

    You found a vulnerability. Report it privately through GitHub and never
    in a public issue or a pull request.

    [:lucide-arrow-right: Read SECURITY.md](https://github.com/tnek213/bwtidy/blob/main/SECURITY.md#reporting-a-vulnerability)

- :lucide-heart-handshake:{ .lg .middle } **Code of conduct**

    ---

    The standards everyone who takes part follows and where to report
    unacceptable behavior.

    [:lucide-arrow-right: Read the code of conduct](https://github.com/tnek213/bwtidy/blob/main/CODE_OF_CONDUCT.md)

</div>

## A diagram in these docs

Any page of these docs can hold a diagram. You write it as a fenced code
block whose language is a diagram type that [Kroki](https://kroki.io/)
supports (such as `mermaid` or `plantuml`). After the language comes
`alt="..."` with a text that says what the diagram shows. This block draws
what the docs build does with a diagram.

````markdown
```mermaid alt="The docs build asks a Kroki server for a diagram it has not cached and puts a picture of it in the page"
sequenceDiagram
  participant Page as Docs page
  participant Build as Docs build
  participant Kroki as Kroki server
  Page->>Build: Code block with alt text
  opt Not in the cache
    Build->>Kroki: Diagram source
    Kroki-->>Build: SVG
  end
  Build->>Page: PNG picture with the alt text
```
````

The built page shows it as this picture.

```mermaid alt="The docs build asks a Kroki server for a diagram it has not cached and puts a picture of it in the page"
sequenceDiagram
  participant Page as Docs page
  participant Build as Docs build
  participant Kroki as Kroki server
  Page->>Build: Code block with alt text
  opt Not in the cache
    Build->>Kroki: Diagram source
    Kroki-->>Build: SVG
  end
  Build->>Page: PNG picture with the alt text
```

The picture is a file of the docs site, so the docs that a running bwtidy
serves fetch nothing from other sites. A diagram without alt text fails the
build, and `scripts/docs-lint` reports it as well.

`scripts/docs-site serve` shows a diagram as its code block. A build shows
the picture, and so does a Kroki preview in your editor (such as a Kroki
extension for VS Code). A build asks a Kroki server only for a picture it
has not cached. `dev/release.md` (Diagrams) names that server and says how
to use your own.

You find the spec, the architecture and the developer guide under
[For contributors](index.md#for-contributors) on the home page.

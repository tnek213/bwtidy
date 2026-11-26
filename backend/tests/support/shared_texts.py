"""The texts that README.md shares with docs/index.md and docker/hub-overview.md (AGENTS.md,
Writing docs), cut out of a file so the repo tests can compare them."""

INTRO_START = (
    "<!-- intro: the same text in README.md, docs/index.md and docker/hub-overview.md -->\n\n"
)
INTRO_END = "\n\n<!-- end of intro -->"


def intro(text: str) -> str:
    start = text.index(INTRO_START) + len(INTRO_START)
    end = text.index(INTRO_END, start)
    return text[start:end]


def disclaimer(text: str) -> str:
    start = text.index("> [!WARNING]\n> **Made with AI.**")
    end = text.index("\n\n", start)
    return text[start:end]


def at_a_glance(text: str) -> str:
    start = text.index("## At a glance\n\n") + len("## At a glance\n\n")
    end = text.index("\n\n", start)
    return text[start:end]


def trademark(text: str) -> str:
    start = text.index("bwtidy is an independent project.")
    end = text.index("\n\n", start)
    return text[start:end]

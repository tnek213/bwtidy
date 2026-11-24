"""The tool note's templates and datatypes as the vault holds them, and how the vault's values
match their datatypes (docs/spec.md, Templates and Datatypes).

sections/matching.py scores an item it is handed. This module asks the cached tool note for the
templates and datatypes, scores every item of the cache, and keeps each result by item id,
column and the datatype's pattern and flags, with the statistics of the checks. A result never
holds a character of a value, and nothing here logs one.
"""

import time
import unicodedata
from typing import Any

from bwtidy.items.patterns import CompiledPattern, FormatResult
from bwtidy.sections import InvalidToolNote
from bwtidy.sections.datatypes import Datatype, InvalidDatatypes, stored_datatypes
from bwtidy.sections.matching import (
    ResultOf,
    best_match,
    find_template,
    format_of,
    lowest_of,
    score_json,
    scores_with,
    scoring,
)
from bwtidy.sections.templates import InvalidTemplates, Template, load_templates
from bwtidy.vault.cache import VaultCache
from bwtidy.vault.statistics import Measure, Statistics
from bwtidy.vault.tool_note import ToolNoteId, find_tool_note

## item id, column, the datatype's pattern and its ignore case and whole value flags
type FormatKey = tuple[str, str, str, bool, bool]


def stored_templates(cache: VaultCache, remembered: ToolNoteId) -> list[Template]:
    """The templates in the tool note. Raises InvalidTemplates when broken."""
    note = find_tool_note(cache, remembered)
    return load_templates(note.get("notes")) if note else []


def template_named(cache: VaultCache, remembered: ToolNoteId, name: str) -> Template:
    """The template of that name in the tool note. Raises ValueError when there is none."""
    try:
        templates = stored_templates(cache, remembered)
    except InvalidTemplates as error:
        raise ValueError(f"the stored templates are not valid: {error}") from None
    template = find_template(templates, name)
    if template is None:
        raise ValueError(f"no template named {name!r}")
    return template


def vault_datatypes(cache: VaultCache, remembered: ToolNoteId) -> list[Datatype]:
    """The datatypes in the tool note. Notes that hold none that can be read hold none."""
    note = find_tool_note(cache, remembered)
    if note is None:
        return []
    try:
        return stored_datatypes(note.get("notes"))
    except (InvalidToolNote, InvalidDatatypes):
        return []


def _measured(compiled: CompiledPattern, text: str) -> Measure:
    started = time.perf_counter()
    result = format_of(compiled, text)
    seconds = time.perf_counter() - started
    return Measure(len(unicodedata.normalize("NFC", text)), seconds, result)


class Formats:
    """How the values of the cache's last load match their datatypes, None for one too long to
    check, and how hard those checks worked (statistics). RAM only: the results go with the
    items they were computed from, both go with the session (SessionManager clears them)."""

    def __init__(self, remembered: ToolNoteId) -> None:
        ## where the lookups of the tool note keep its id
        self.remembered = remembered
        self.results: dict[FormatKey, FormatResult | None] = {}
        self.statistics = Statistics()
        ## the cache's load the results are of
        self._load = 0

    def clear(self) -> None:
        self.results = {}
        self.statistics.clear()

    def result_of(self, cache: VaultCache) -> ResultOf:
        """The lookup scoring uses on the items of the cache: a kept result, else the lowest
        over the texts, kept and recorded in the statistics. An item without an id is scored
        every time and recorded nowhere."""
        if cache.loads != self._load:
            self._load = cache.loads
            self.results = {}
        results = self.results

        def lookup(
            item: dict[str, Any], column: str, texts: list[str], datatype: Datatype
        ) -> FormatResult | None:
            item_id = item.get("id")
            key: FormatKey = (
                str(item_id),
                column,
                datatype.pattern,
                datatype.ignoreCase,
                datatype.wholeValue,
            )
            statistics = self.statistics if item_id else None
            known = bool(item_id) and key in results
            ## two datatypes with one pattern share a result: the second is scored too, to get
            ## its own row
            if known and (statistics is None or statistics.has_row(key[0], column, datatype.id)):
                return results[key]
            compiled = datatype.compiled
            assert compiled is not None
            measures = [_measured(compiled, text) for text in texts]
            lowest = lowest_of([m.result for m in measures])
            if item_id:
                results[key] = lowest
            if statistics is not None:
                name = item.get("name")
                shown = name if isinstance(name, str) else ""
                statistics.record(key[0], shown, column, datatype, compiled, measures, lowest)
            return lowest

        return lookup


def template_matches(
    cache: VaultCache, formats: Formats, templates: list[Template], *, for_mcp: bool = False
) -> dict[str, dict[str, Any]]:
    """Per item id: the matching template's name (or None) and the score for every template.

    The tool note is left out. With for_mcp a secret column's format term is whether it is set
    and it has no formats entry, so MCP learns nothing about a secret beyond today. Without it
    (the UI) secret columns are scored too, with firstEdit and expected but never characters of
    the value.
    """
    note = find_tool_note(cache, formats.remembered)
    datatypes = vault_datatypes(cache, formats.remembered)
    usable = scoring(datatypes, formats.result_of(cache), for_mcp)
    result: dict[str, dict[str, Any]] = {}
    for item in cache.items:
        item_id = item.get("id")
        if not item_id or item is note:
            continue
        item_scores = scores_with(item, templates, usable)
        match = best_match(templates, item_scores)
        result[item_id] = {
            "match": match.name if match else None,
            "scores": [score_json(s) for s in item_scores],
        }
    return result


def refresh_formats(cache: VaultCache, formats: Formats) -> None:
    """Compute how every value matches its datatype, after a load of the vault (the datatypes
    and the templates change only through one). Nothing here logs or keeps a value."""
    formats.statistics.start_reload(vault_datatypes(cache, formats.remembered))
    try:
        templates = stored_templates(cache, formats.remembered)
    except InvalidTemplates:
        return
    if any(c.datatype is not None for t in templates for c in t.coordinates):
        template_matches(cache, formats, templates)

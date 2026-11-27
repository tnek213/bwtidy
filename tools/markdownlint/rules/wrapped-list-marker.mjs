// BWT001: a wrapped line of a list item's text that starts with a list marker.
//
// The docs site renders Markdown with Python-Markdown (Zensical), whose list processor starts a
// new item at every line of a list block that matches its CHILD_RE
// (^[ ]{0,3}((\d+\.)|[*+-])[ ]+(.*)) and a nested one at a line that matches its INDENT_RE
// (the same marker after 4 to 7 spaces), even in the middle of an item's text. GitHub
// (CommonMark) keeps such a line in the paragraph where a list cannot interrupt it, as with a
// number other than 1, so markdownlint's own rules see nothing wrong. Python-Markdown splits only
// the paragraph that starts on the item's marker line: a later paragraph of the item is parsed on
// its own and keeps its lines.

const MARKER = /^(\d+\.|[*+-]) +/
const QUOTE_PREFIX = /^ {0,3}> ?/
const LISTS = new Set(['listOrdered', 'listUnordered'])
// INDENT_RE allows at most 7 spaces before a marker, a line indented further stays in the text
const MAX_INDENT = 7

const all = (token) => [token, ...token.children.flatMap(all)]

const indentOf = (text) => text.length - text.trimStart().length

// the line as Python-Markdown's list processor sees it, without the blockquote markers around the
// list
const unquoted = (line, quotes) => {
  let text = line
  for (let i = 0; i < quotes; i++) {
    text = text.replace(QUOTE_PREFIX, '')
  }
  return text
}

const quoteDepth = (token) => {
  let depth = 0
  for (let up = token.parent; up; up = up.parent) {
    if (up.type === 'blockQuote') {
      depth++
    }
  }
  return depth
}

// the first paragraph of each item of LIST, the one that starts on the item's marker line
const firstParagraphs = function* (list) {
  let prefix = null
  for (const child of list.children) {
    if (child.type === 'listItemPrefix') {
      prefix = child
    } else if (child.type === 'content' && prefix && child.startLine === prefix.startLine) {
      const paragraph = child.children.find((token) => token.type === 'paragraph')
      if (paragraph && paragraph.startLine === prefix.startLine) {
        yield paragraph
      }
    }
  }
}

/** @type {import("markdownlint").Rule} */
export default {
  names: ['BWT001', 'wrapped-line-list-marker'],
  description: 'Wrapped line of a list item starts with a list marker',
  tags: ['bwtidy', 'ul', 'ol'],
  parser: 'micromark',
  function: (params, onError) => {
    const lists = params.parsers.micromark.tokens.flatMap(all).filter((t) => LISTS.has(t.type))
    for (const list of lists) {
      const quotes = quoteDepth(list)
      for (const paragraph of firstParagraphs(list)) {
        const markerIndent = indentOf(unquoted(params.lines[paragraph.startLine - 1], quotes))
        for (let number = paragraph.startLine + 1; number <= paragraph.endLine; number++) {
          const raw = params.lines[number - 1]
          const line = unquoted(raw, quotes)
          const indent = indentOf(line)
          const marker = line.slice(indent).match(MARKER)
          if (!marker || indent - markerIndent > MAX_INDENT) {
            continue
          }
          onError({
            lineNumber: number,
            detail:
              'Move the line break so the line does not start with a list marker, ' +
              'the docs site starts a new list item there',
            context: line.trim(),
            range: [raw.length - line.length + indent + 1, marker[1].length],
          })
        }
      }
    }
  },
}

import { Typography, type TypographyProps } from '@mui/material'
import {
  cloneElement,
  createElement,
  Fragment,
  isValidElement,
  useCallback,
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
  type ElementType,
  type ReactNode,
} from 'react'
import { lineBreaks } from './lineBreaks.ts'

// The widest a line of running text gets, in characters: the top of the usual reading range of 45 to 75.
export const PROSE_WIDTH = '75ch'

const WORD = /[^ \t\n\r\f]+/g

// The text the children render, as far as the React tree tells. A component that renders text of its own makes
// it differ from the paragraph's textContent.
function textOf(node: ReactNode): string {
  if (typeof node === 'string' || typeof node === 'number' || typeof node === 'bigint') {
    return String(node)
  }
  if (Array.isArray(node)) {
    return node.map(textOf).join('')
  }
  if (isValidElement<{ children?: ReactNode }>(node)) {
    return textOf(node.props.children)
  }
  return ''
}

function width(range: Range): number {
  let total = 0
  for (const rect of range.getClientRects()) {
    total += rect.width
  }
  return total
}

// The width the paragraph takes without the breaks: with them it shrinks to its longest line wherever its width
// follows its content (a dialog, an alert sized to its text), and balancing that again would shrink it further.
function naturalWidth(element: HTMLElement): number {
  const own = [...element.querySelectorAll<HTMLElement>('br[data-prose-break]')]
  for (const br of own) {
    br.style.display = 'none'
  }
  const natural = element.clientWidth
  for (const br of own) {
    br.style.removeProperty('display')
  }
  return natural
}

// The character offsets in the paragraph's text where the balanced lines start, or none when the paragraph cannot
// be balanced (no layout, as in jsdom, or text the React tree does not know).
function measure(element: HTMLElement, text: string): number[] {
  if (element.textContent !== text) {
    return []
  }
  const max = naturalWidth(element) - 1
  if (max <= 0) {
    return []
  }
  // The words are measured with the breaks shown: Chromium measures text ranges wrong next to a br hidden with
  // display none.
  const nodes: Text[] = []
  const starts: number[] = []
  const walker = document.createTreeWalker(element, NodeFilter.SHOW_TEXT)
  let length = 0
  for (let node = walker.nextNode(); node !== null; node = walker.nextNode()) {
    nodes.push(node as Text)
    starts.push(length)
    length += (node as Text).length
  }
  const range = document.createRange()
  let first = 0
  // The width of the text from start to end, summed over the text nodes it runs over (a word can start in one
  // element and end in the next).
  const measured = (start: number, end: number) => {
    while (first < nodes.length - 1 && starts[first + 1] <= start) {
      first++
    }
    let total = 0
    for (let index = first; index < nodes.length && starts[index] < end; index++) {
      const from = Math.max(start - starts[index], 0)
      const to = Math.min(end - starts[index], nodes[index].length)
      if (to > from) {
        range.setStart(nodes[index], from)
        range.setEnd(nodes[index], to)
        total += width(range)
      }
    }
    return total
  }
  const words = [...text.matchAll(WORD)].map((match) => ({ start: match.index, end: match.index + match[0].length }))
  const widths = words.map(({ start, end }) => measured(start, end))
  // A space at the end of a line measures 0, so take the usual width of the spaces between the words.
  first = 0
  const spaces = words
    .slice(1)
    .map(({ start }) => measured(start - 1, start))
    .filter((space) => space > 0)
    .sort((a, b) => a - b)
  const space = spaces.length > 0 ? spaces[Math.floor(spaces.length / 2)] : 0
  return lineBreaks(widths, space, max).map((index) => words[index].start)
}

// The children with a br after the space before each word that starts a line, so the text itself stays the same.
function withBreaks(children: ReactNode, offsets: number[]): ReactNode[] {
  let position = 0
  let next = 0
  const lineBreak = () => {
    const offset = offsets[next++]
    return <br key={`break-${offset}`} data-prose-break="" />
  }
  const walk = (node: ReactNode): ReactNode[] => {
    if (Array.isArray(node)) {
      return node.flatMap(walk)
    }
    if (typeof node === 'string' || typeof node === 'number' || typeof node === 'bigint') {
      const text = String(node)
      const parts: ReactNode[] = []
      let from = 0
      while (next < offsets.length && offsets[next] < position + text.length) {
        const at = offsets[next] - position
        if (at > from) {
          parts.push(text.slice(from, at))
        }
        parts.push(lineBreak())
        from = at
      }
      if (from < text.length) {
        parts.push(text.slice(from))
      }
      position += text.length
      return parts
    }
    if (isValidElement<{ children?: ReactNode }>(node)) {
      const length = textOf(node).length
      if (length === 0) {
        return [node]
      }
      const parts: ReactNode[] = []
      while (next < offsets.length && offsets[next] === position) {
        parts.push(lineBreak())
      }
      if (next < offsets.length && offsets[next] < position + length) {
        parts.push(cloneElement(node, undefined, ...walk(node.props.children)))
      } else {
        parts.push(node)
        position += length
      }
      return parts
    }
    return [node]
  }
  return walk(children)
}

function sameOffsets(a: number[], b: number[]): boolean {
  return a.length === b.length && a.every((offset, index) => offset === b[index])
}

// A paragraph of running text. Tables, the grid and forms do not use it and keep their width. Its lines are
// balanced: the breaks are chosen for the whole paragraph (lineBreaks) so that every line but the last comes out
// close in length, and set with a br after the space, measured again whenever the paragraph's width or text changes.
export default function Prose<C extends ElementType = 'p'>({ sx, children, ...props }: TypographyProps<C, { component?: C }>) {
  const ref = useRef<HTMLElement>(null)
  const text = textOf(children)
  const shown = useRef(text)
  const [lines, setLines] = useState<{ text: string; offsets: number[] }>({ text, offsets: [] })

  const balance = useCallback(() => {
    const element = ref.current
    if (element === null) {
      return
    }
    const current = shown.current
    const offsets = measure(element, current)
    setLines((previous) =>
      sameOffsets(previous.offsets, offsets) && (offsets.length === 0 || previous.text === current)
        ? previous
        : { text: current, offsets },
    )
  }, [])

  useLayoutEffect(() => {
    shown.current = text
    balance()
  }, [text, balance])

  useEffect(() => {
    const element = ref.current
    if (element === null) {
      return
    }
    let live = true
    const observer = typeof ResizeObserver === 'undefined' ? null : new ResizeObserver(balance)
    observer?.observe(element)
    // A paragraph sized to its text keeps its width when the window grows, so the window's own size counts too.
    window.addEventListener('resize', balance)
    if ('fonts' in document) {
      void document.fonts.ready.then(() => {
        if (live) {
          balance()
        }
      })
    }
    return () => {
      live = false
      observer?.disconnect()
      window.removeEventListener('resize', balance)
    }
  }, [balance])

  const offsets = lines.text === text ? lines.offsets : []
  return (
    <Typography
      ref={ref}
      variant="body2"
      color="text.secondary"
      {...props}
      sx={[{ maxWidth: PROSE_WIDTH }, ...(Array.isArray(sx) ? sx : [sx])]}
    >
      {offsets.length === 0 ? children : createElement(Fragment, null, ...withBreaks(children, offsets))}
    </Typography>
  )
}

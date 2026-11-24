import { render } from '@testing-library/react'
import { afterEach, beforeEach, expect, test, vi } from 'vitest'
import Prose from './Prose.tsx'

// jsdom has no layout: every character is 10 px wide and the paragraph 201 px, so a line holds 20 characters.
beforeEach(() => {
  vi.spyOn(HTMLElement.prototype, 'clientWidth', 'get').mockReturnValue(201)
  Range.prototype.getClientRects = function (this: Range) {
    return [{ width: this.toString().length * 10 }] as unknown as DOMRectList
  }
})

afterEach(() => {
  vi.restoreAllMocks()
  delete (Range.prototype as Partial<Range>).getClientRects
})

// The text of each line, cut at the breaks.
function lines(element: HTMLElement): string[] {
  const result = ['']
  const walker = document.createTreeWalker(element, NodeFilter.SHOW_TEXT | NodeFilter.SHOW_ELEMENT)
  for (let node = walker.nextNode(); node !== null; node = walker.nextNode()) {
    if (node instanceof HTMLBRElement) {
      result.push('')
    } else if (node instanceof Text) {
      result[result.length - 1] += node.data
    }
  }
  return result
}

test('the lines are balanced with a br after the space and the text stays the same', () => {
  const error = vi.spyOn(console, 'error')
  const text = `${'a'.repeat(12)} ${'b'.repeat(7)} ${'c'.repeat(7)} ${'d'.repeat(15)}`
  const { container } = render(<Prose>{text}</Prose>)
  const paragraph = container.querySelector('p')!
  expect(paragraph.textContent).toBe(text)
  // Greedy filling would give a 20 character line and then a 7 character one.
  expect(lines(paragraph)).toEqual([`${'a'.repeat(12)} `, `${'b'.repeat(7)} ${'c'.repeat(7)} `, 'd'.repeat(15)])
  expect(error).not.toHaveBeenCalled()
})

test('a word that runs over several elements is measured whole and elements get their breaks inside', () => {
  const error = vi.spyOn(console, 'error')
  const { container } = render(
    <Prose>
      Decrypt it on a tmpfs (<code>age -d -o backup.zip NAME.zip.age</code>), <strong>login.username</strong>, so
      no copy is left.
    </Prose>,
  )
  const paragraph = container.querySelector('p')!
  expect(paragraph.textContent).toBe(
    'Decrypt it on a tmpfs (age -d -o backup.zip NAME.zip.age), login.username, so no copy is left.',
  )
  const shown = lines(paragraph)
  expect(shown.length).toBeGreaterThan(1)
  expect(shown.join('')).toBe(paragraph.textContent)
  for (const line of shown) {
    expect(line.trimEnd().length).toBeLessThanOrEqual(20)
  }
  expect(paragraph.querySelector('code')!.textContent).toBe('age -d -o backup.zip NAME.zip.age')
  expect(paragraph.querySelector('code br')).not.toBeNull()
  expect(error).not.toHaveBeenCalled()
})

function OwnText() {
  return <span>text the tree does not know about</span>
}

test('a paragraph with a component that renders text of its own is left as it is', () => {
  const { container } = render(
    <Prose>
      Some words before <OwnText /> and some words after it.
    </Prose>,
  )
  expect(container.querySelector('br')).toBeNull()
})

test('a paragraph that fits on one line gets no breaks', () => {
  const { container } = render(<Prose>Short words.</Prose>)
  expect(container.querySelector('br')).toBeNull()
  expect(container.querySelector('p')!.textContent).toBe('Short words.')
})

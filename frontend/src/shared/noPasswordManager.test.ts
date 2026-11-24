import { expect, test } from 'vitest'
import { markCellEditor } from './noPasswordManager.ts'

function editingCell(tag: 'input' | 'textarea'): HTMLElement {
  const cell = document.createElement('div')
  cell.className = 'ag-cell ag-cell-inline-editing'
  cell.append(document.createElement(tag))
  return cell
}

function expectOptedOut(element: Element | null) {
  expect(element).toHaveAttribute('autocomplete', 'off')
  expect(element).toHaveAttribute('data-bwignore', 'true')
  expect(element).toHaveAttribute('data-1p-ignore', 'true')
  expect(element).toHaveAttribute('data-lpignore', 'true')
  expect(element).toHaveAttribute('data-form-type', 'other')
}

test('an editor already in the grid is marked at once', () => {
  const root = document.createElement('div')
  root.append(editingCell('input'))
  markCellEditor(root)
  expectOptedOut(root.querySelector('input'))
})

test('an editor rendered after editing started is marked when it shows', async () => {
  const root = document.createElement('div')
  const other = document.createElement('input')
  root.append(other)
  markCellEditor(root)
  root.append(editingCell('textarea'))
  await new Promise((resolve) => setTimeout(resolve, 0))
  expectOptedOut(root.querySelector('textarea'))
  // An input outside the editing cell (a row's selection checkbox) is left alone.
  expect(other).not.toHaveAttribute('data-bwignore')
})

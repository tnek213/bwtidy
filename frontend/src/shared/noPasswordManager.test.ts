import { expect, test } from 'vitest'
import { markCellEditors } from './noPasswordManager.ts'

function editingCell(tag: 'input' | 'textarea', className = 'ag-cell ag-cell-inline-editing'): HTMLElement {
  const cell = document.createElement('div')
  cell.className = className
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
  const stop = markCellEditors(root)
  expectOptedOut(root.querySelector('input'))
  stop()
})

test('an editor is marked before any other task runs after it is added', async () => {
  const root = document.createElement('div')
  const other = document.createElement('input')
  root.append(other)
  const stop = markCellEditors(root)
  root.append(editingCell('textarea'))
  // a microtask after the change, the observer's turn: no timer or other task has run
  await Promise.resolve()
  expectOptedOut(root.querySelector('textarea'))
  // An input outside the editing cell (a row's selection checkbox) is left alone.
  expect(other).not.toHaveAttribute('data-bwignore')
  stop()
})

test('every later editor is marked too, a popup editor as well, until the cleanup', async () => {
  const root = document.createElement('div')
  const stop = markCellEditors(root)
  for (const className of ['ag-cell ag-cell-inline-editing', 'ag-popup-editor']) {
    const cell = editingCell('input', className)
    root.append(cell)
    await Promise.resolve()
    expectOptedOut(cell.querySelector('input'))
    cell.remove()
  }
  stop()
  const after = editingCell('input')
  root.append(after)
  await Promise.resolve()
  expect(after.querySelector('input')).not.toHaveAttribute('data-bwignore')
})

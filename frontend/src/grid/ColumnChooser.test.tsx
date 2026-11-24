import { fireEvent, render, screen, within } from '@testing-library/react'
import { expect, test, vi } from 'vitest'
import ColumnChooser from './ColumnChooser.tsx'
import type { GridColumn } from '../api/grid.ts'

function column(id: string, group: GridColumn['group']): GridColumn {
  return { id, label: id, group, secret: false, multi: false, readOnly: false }
}

const columns: GridColumn[] = [
  column('name', 'item'),
  column('type', 'item'),
  column('notes', 'item'),
  column('login.username', 'login'),
  column('login.password', 'login'),
  column('card.number', 'card'),
]

function renderChooser(hidden: string[], onChange = vi.fn(), onSelected = vi.fn()) {
  const view = render(
    <ColumnChooser
      columns={columns}
      hidden={new Set(hidden)}
      onChange={onChange}
      selected={new Set()}
      onSelected={onSelected}
      onMergeSelected={vi.fn()}
    />,
  )
  fireEvent.click(screen.getByRole('button', { name: /^Columns/ }))
  return { ...view, onChange, onSelected }
}

function groupBox(label: string) {
  return screen.getByRole('group', { name: label })
}

function groupCheckbox(label: string) {
  return within(groupBox(label)).getByRole('checkbox', { name: label })
}

function lastHidden(onChange: ReturnType<typeof vi.fn>): string[] {
  expect(onChange).toHaveBeenCalledTimes(1)
  return [...(onChange.mock.calls[0][0] as Set<string>)].sort()
}

test('unchecking Login hides every login column in one change', () => {
  const { onChange } = renderChooser([])
  expect(groupCheckbox('Login')).toBeChecked()
  fireEvent.click(groupCheckbox('Login'))
  expect(lastHidden(onChange)).toEqual(['login.password', 'login.username'])
})

test('checking Login again shows its columns and keeps other hidden ones', () => {
  const { onChange } = renderChooser(['login.password', 'login.username', 'card.number'])
  expect(groupCheckbox('Login')).not.toBeChecked()
  expect(groupCheckbox('Login')).not.toHaveProperty('indeterminate', true)
  fireEvent.click(groupCheckbox('Login'))
  expect(lastHidden(onChange)).toEqual(['card.number'])
})

test('a group with some columns hidden is indeterminate and a click hides the rest', () => {
  const { onChange } = renderChooser(['login.password'])
  const box = groupCheckbox('Login')
  expect(box).toHaveAttribute('data-indeterminate', 'true')
  fireEvent.click(box)
  expect(lastHidden(onChange)).toEqual(['login.password', 'login.username'])
})

test('the Item checkbox never hides name', () => {
  const { onChange } = renderChooser([])
  expect(groupCheckbox('Item')).toBeChecked()
  fireEvent.click(groupCheckbox('Item'))
  expect(lastHidden(onChange)).toEqual(['notes', 'type'])
})

test('the Item checkbox is unchecked when only name is shown, and a click shows the rest', () => {
  const { onChange } = renderChooser(['type', 'notes'])
  expect(groupCheckbox('Item')).not.toBeChecked()
  expect(groupCheckbox('Item')).toHaveAttribute('data-indeterminate', 'false')
  fireEvent.click(groupCheckbox('Item'))
  expect(lastHidden(onChange)).toEqual([])
})

test('with Find column text the group checkbox acts on the matching columns only', () => {
  const { onChange } = renderChooser([])
  fireEvent.change(screen.getByLabelText('Find column'), { target: { value: 'user' } })
  expect(within(groupBox('Login')).queryByRole('checkbox', { name: 'login.password' })).toBeNull()
  fireEvent.click(groupCheckbox('Login'))
  expect(lastHidden(onChange)).toEqual(['login.username'])
})

test('the group checkbox leaves the merge selection alone', () => {
  const { onChange, onSelected } = renderChooser([])
  fireEvent.click(groupCheckbox('Login'))
  expect(onChange).toHaveBeenCalledTimes(1)
  expect(onSelected).not.toHaveBeenCalled()
})

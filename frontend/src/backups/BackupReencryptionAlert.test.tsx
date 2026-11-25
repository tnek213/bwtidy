import { fireEvent, render, screen } from '@testing-library/react'
import { expect, test } from 'vitest'
import BackupReencryptionAlert from './BackupReencryptionAlert.tsx'

const FAILED = ['20200101T000000Z.zip.age', '20250606T060606Z.zip.age']

test('names every file that was not re-encrypted', () => {
  render(<BackupReencryptionAlert result={{ done: 3, failed: FAILED }} />)
  const alert = screen.getByRole('alert')
  expect(alert).toHaveTextContent('Backups not re-encrypted')
  for (const name of FAILED) {
    expect(alert).toHaveTextContent(name)
  }
})

test('shows nothing without failures or without a result', () => {
  const { rerender } = render(<BackupReencryptionAlert result={{ done: 3, failed: [] }} />)
  expect(screen.queryByRole('alert')).not.toBeInTheDocument()
  rerender(<BackupReencryptionAlert result={undefined} />)
  expect(screen.queryByRole('alert')).not.toBeInTheDocument()
})

test('can be closed', () => {
  render(<BackupReencryptionAlert result={{ done: 0, failed: FAILED.slice(0, 1) }} />)
  fireEvent.click(screen.getByRole('button', { name: 'Close' }))
  expect(screen.queryByRole('alert')).not.toBeInTheDocument()
})

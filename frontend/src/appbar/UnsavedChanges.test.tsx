import { fireEvent, render, screen } from '@testing-library/react'
import { expect, test } from 'vitest'
import { AutoCommitContext, type AutoCommitState, type AutoCommitStatus, useAutoCommit } from '../commit/autoCommit.ts'
import UnsavedChanges from './UnsavedChanges.tsx'

const idle: AutoCommitStatus = {
  afterSeconds: 10,
  maxEntries: 20,
  pending: 0,
  committing: true,
  nextCommitAt: null,
  commitCount: 0,
  phase: null,
}

function Committing({ status }: { status: AutoCommitStatus }) {
  const state: AutoCommitState = { ...useAutoCommit(), status, committing: true }
  return (
    <AutoCommitContext value={state}>
      <UnsavedChanges />
    </AutoCommitContext>
  )
}

test.each([
  ['Saving changes', { phase: null }],
  ['Syncing with the server', { phase: 'syncing' }],
  ['Taking a backup', { phase: 'backingUp' }],
  ['Writing 3 of 5 changes', { phase: 'writing', done: 2, total: 5 }],
  ['Writing the change', { phase: 'writing', done: 0, total: 1 }],
  ['Reloading the vault', { phase: 'reloading' }],
] as const)('the spinner of a running commit says %s, also on hover', async (text, phase) => {
  render(<Committing status={{ ...idle, ...phase }} />)

  const spinner = screen.getByRole('progressbar', { name: text })
  fireEvent.mouseOver(spinner)
  expect(await screen.findByRole('tooltip')).toHaveTextContent(text)
})

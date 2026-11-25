import { fireEvent, render, screen } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { LAYOUT_STORAGE_KEY, LayoutProvider, useLayout } from './layout.tsx'

function Probe() {
  const { layout, setLayout } = useLayout()
  return (
    <>
      <p>layout: {layout}</p>
      <button type="button" onClick={() => setLayout('compact')}>
        Compact
      </button>
      <button type="button" onClick={() => setLayout('normal')}>
        Normal
      </button>
    </>
  )
}

function renderProbe() {
  return render(
    <LayoutProvider>
      <Probe />
    </LayoutProvider>,
  )
}

function shown(): string {
  return screen.getByText(/^layout:/).textContent ?? ''
}

describe('LayoutProvider', () => {
  beforeEach(() => localStorage.clear())
  afterEach(() => {
    localStorage.clear()
    vi.restoreAllMocks()
  })

  it('is normal with no stored value', () => {
    renderProbe()
    expect(shown()).toBe('layout: normal')
  })

  it('counts an unknown stored value as normal', () => {
    localStorage.setItem(LAYOUT_STORAGE_KEY, 'cosy')
    renderProbe()
    expect(shown()).toBe('layout: normal')
  })

  it('reads a stored compact choice', () => {
    localStorage.setItem(LAYOUT_STORAGE_KEY, 'compact')
    renderProbe()
    expect(shown()).toBe('layout: compact')
  })

  it('is normal when localStorage throws', () => {
    vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => {
      throw new Error('no storage')
    })
    renderProbe()
    expect(shown()).toBe('layout: normal')
  })

  it('writes a compact choice under bwtidy.layout and clears it for normal', () => {
    renderProbe()
    fireEvent.click(screen.getByRole('button', { name: 'Compact' }))
    expect(shown()).toBe('layout: compact')
    expect(localStorage.getItem(LAYOUT_STORAGE_KEY)).toBe('compact')
    fireEvent.click(screen.getByRole('button', { name: 'Normal' }))
    expect(shown()).toBe('layout: normal')
    expect(localStorage.getItem(LAYOUT_STORAGE_KEY)).toBeNull()
  })

  it('keeps the choice for the page even when the write fails', () => {
    vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
      throw new Error('no storage')
    })
    renderProbe()
    fireEvent.click(screen.getByRole('button', { name: 'Compact' }))
    expect(shown()).toBe('layout: compact')
  })
})

import { fireEvent, screen, within } from '@testing-library/react'
import { afterEach, expect, test, vi } from 'vitest'
import { jsonResponse, mockFetch, renderWithQuery } from '../test-utils.tsx'
import AboutPage from './AboutPage.tsx'
import { RUNTIME_CREDITS, TOOL_CREDITS } from './credits.ts'
import { SOURCE_URL } from './links.ts'
import thirdParty from './thirdParty.json'

afterEach(() => {
  vi.unstubAllGlobals()
})

const CREDITS = [...RUNTIME_CREDITS, ...TOOL_CREDITS]

function renderPage() {
  mockFetch({ 'GET /api/health': () => jsonResponse(200, { status: 'ok', version: '0.1.0-alpha.1 (261028 abc1234)' }) })
  renderWithQuery(<AboutPage />)
}

function expectNewTab(link: HTMLElement, href: string) {
  expect(link).toHaveAttribute('href', href)
  expect(link).toHaveAttribute('target', '_blank')
  expect(link).toHaveAttribute('rel', expect.stringContaining('noopener'))
}

test('shows the version and the commit from /api/health', async () => {
  renderPage()
  expect(screen.getByRole('heading', { name: 'About bwtidy' })).toBeInTheDocument()
  expect(await screen.findByText('Version 0.1.0-alpha.1 (261028 abc1234)')).toBeInTheDocument()
})

test('links the license and the source code in a new tab', () => {
  renderPage()
  expect(screen.getByText(/GNU Affero General Public License,\s+version 3 only/)).toBeInTheDocument()
  expect(screen.getByText(/WITHOUT ANY WARRANTY/)).toBeInTheDocument()
  expectNewTab(screen.getByRole('link', { name: 'LICENSE' }), `${SOURCE_URL}/blob/main/LICENSE`)
  expectNewTab(screen.getByRole('link', { name: 'Source code' }), SOURCE_URL)
})

test('thanks every credited project with a link, its language, license and use', () => {
  renderPage()
  for (const credit of CREDITS) {
    const link = screen.getByRole('link', { name: credit.name })
    expectNewTab(link, credit.url)
    const item = link.closest('li')
    expect(item).toHaveTextContent(`(${credit.language}, ${credit.license}): ${credit.usedFor}.`)
  }
})

test('every direct dependency has a credit with its license', () => {
  const credited = new Map(CREDITS.flatMap((credit) => (credit.packages ?? []).map((name) => [name, credit])))
  const direct = thirdParty.filter((entry) => entry.direct)
  expect(direct.length).toBeGreaterThan(0)
  for (const entry of direct) {
    expect(credited.get(entry.name)?.name, entry.name).toBeDefined()
  }
  for (const [name, credit] of credited) {
    const entry = thirdParty.find((one) => one.name === name)
    expect(entry?.license, name).toBe(credit.license)
  }
})

test('the accordion lists every package of thirdParty.json, closed until opened', () => {
  renderPage()
  const summary = screen.getByRole('button', { name: `Every package and its license (${thirdParty.length})` })
  expect(summary).toHaveAttribute('aria-expanded', 'false')
  fireEvent.click(summary)
  expect(summary).toHaveAttribute('aria-expanded', 'true')
  const table = screen.getByRole('table', { name: 'Every package and its license' })
  const rows = within(table).getAllByRole('row')
  expect(rows[0]).toHaveTextContent('PackageVersionFromLicense')
  expect(rows).toHaveLength(thirdParty.length + 1)
  expect(within(table).getByRole('row', { name: /^fastapi \S+ PyPI MIT$/ })).toBeInTheDocument()
})

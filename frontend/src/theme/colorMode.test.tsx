import { fireEvent, render, screen } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { COLOR_MODE_COOKIE, ColorModeProvider, useColorMode } from './colorMode.tsx'

function stubMatchMedia(dark: boolean) {
  vi.stubGlobal(
    'matchMedia',
    vi.fn((query: string) => ({
      matches: query.includes('dark') && dark,
      media: query,
      onchange: null,
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
      addListener: vi.fn(),
      removeListener: vi.fn(),
      dispatchEvent: vi.fn(),
    })),
  )
}

function clearCookie() {
  document.cookie = `${COLOR_MODE_COOKIE}=; Path=/; Max-Age=0`
}

function Probe() {
  const { mode, setting, setSetting } = useColorMode()
  return (
    <>
      <p>
        mode: {mode}, setting: {setting}
      </p>
      <button type="button" onClick={() => setSetting('light')}>
        Light
      </button>
      <button type="button" onClick={() => setSetting('dark')}>
        Dark
      </button>
      <button type="button" onClick={() => setSetting('system')}>
        System
      </button>
    </>
  )
}

function renderProbe() {
  return render(
    <ColorModeProvider>
      <Probe />
    </ColorModeProvider>,
  )
}

function shown(): string {
  return screen.getByText(/^mode:/).textContent ?? ''
}

describe('ColorModeProvider', () => {
  beforeEach(clearCookie)
  afterEach(() => {
    clearCookie()
    vi.unstubAllGlobals()
  })

  it('follows a dark OS without a cookie, as the system setting', () => {
    stubMatchMedia(true)
    renderProbe()
    expect(shown()).toBe('mode: dark, setting: system')
  })

  it('follows a light OS without a cookie, as the system setting', () => {
    stubMatchMedia(false)
    renderProbe()
    expect(shown()).toBe('mode: light, setting: system')
  })

  it('lets the cookie win over the OS', () => {
    stubMatchMedia(true)
    document.cookie = `${COLOR_MODE_COOKIE}=light; Path=/`
    renderProbe()
    expect(shown()).toBe('mode: light, setting: light')
  })

  it('counts an unknown cookie value as system', () => {
    stubMatchMedia(true)
    document.cookie = `${COLOR_MODE_COOKIE}=purple; Path=/`
    renderProbe()
    expect(shown()).toBe('mode: dark, setting: system')
  })

  it('writes the cookie for Light and Dark', () => {
    stubMatchMedia(false)
    const setter = vi.spyOn(document, 'cookie', 'set')
    renderProbe()
    fireEvent.click(screen.getByRole('button', { name: 'Dark' }))
    expect(shown()).toBe('mode: dark, setting: dark')
    expect(document.cookie).toContain(`${COLOR_MODE_COOKIE}=dark`)
    const written = String(setter.mock.calls.at(-1)?.[0])
    expect(written).toContain('Path=/')
    expect(written).toContain('SameSite=Lax')
    expect(written).toContain('Max-Age=31536000')
    fireEvent.click(screen.getByRole('button', { name: 'Light' }))
    expect(shown()).toBe('mode: light, setting: light')
    expect(document.cookie).toContain(`${COLOR_MODE_COOKIE}=light`)
    setter.mockRestore()
  })

  it('deletes the cookie for System and follows the OS again', () => {
    stubMatchMedia(true)
    document.cookie = `${COLOR_MODE_COOKIE}=light; Path=/`
    const setter = vi.spyOn(document, 'cookie', 'set')
    renderProbe()
    expect(shown()).toBe('mode: light, setting: light')
    fireEvent.click(screen.getByRole('button', { name: 'System' }))
    expect(shown()).toBe('mode: dark, setting: system')
    expect(document.cookie).not.toContain(COLOR_MODE_COOKIE)
    const written = String(setter.mock.calls.at(-1)?.[0])
    expect(written).toContain('Path=/')
    expect(written).toContain('Max-Age=0')
    setter.mockRestore()
  })
})

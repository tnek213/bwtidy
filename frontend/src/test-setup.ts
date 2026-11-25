import '@testing-library/jest-dom/vitest'
import { cleanup, configure } from '@testing-library/react'
import { afterEach } from 'vitest'

// Vitest runs without globals, so Testing Library cannot register its own cleanup.
afterEach(() => {
  cleanup()
})

// The first AG Grid render of a file can take over a second on a busy machine,
// more than the default timeout of the find queries.
configure({ asyncUtilTimeout: 5000 })

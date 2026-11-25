/// <reference types="vitest/config" />
import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

export default defineConfig({
  plugins: [react()],
  server: {
    // The gateway serves /api, /gateway and the docs site at /docs in production. In dev, Vite proxies
    // them. BWTIDY_API points it at the backend of the dev stack, whose port scripts/dev-vault picks
    // per worktree.
    proxy: {
      '/api': process.env.BWTIDY_API ?? 'http://127.0.0.1:8000',
      '/gateway': process.env.BWTIDY_API ?? 'http://127.0.0.1:8000',
      '/docs': process.env.BWTIDY_API ?? 'http://127.0.0.1:8000',
    },
  },
  test: {
    environment: 'jsdom',
    // The tests that wait on a poll or a delay move a fake clock (waitForPolls in test-utils.tsx),
    // but a test that renders AG Grid still takes up to about 4 s while every core runs a test file.
    // Testing Library's asyncUtilTimeout (test-setup.ts) stays below it, so a findBy fails with its
    // own message first.
    testTimeout: 10_000,
    setupFiles: ['./src/test-setup.ts'],
  },
})

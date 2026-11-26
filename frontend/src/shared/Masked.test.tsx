import { render, screen } from '@testing-library/react'
import { expect, test } from 'vitest'
import Masked, { MASKED_NAME } from './Masked.tsx'
import { MASK } from './values.ts'

test('the mask shows its dots and a screen reader says Hidden for them', () => {
  render(<Masked data-testid="mask" />)
  const mask = screen.getByRole('img', { name: MASKED_NAME })
  expect(mask).toHaveTextContent(MASK)
  expect(mask).toBe(screen.getByTestId('mask'))
})

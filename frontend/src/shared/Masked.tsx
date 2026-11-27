import { Box, type BoxProps } from '@mui/material'
import { MASK } from './values.ts'

// What a screen reader says for the mask, which it would otherwise read out dot by dot.
export const MASKED_NAME = 'Hidden'

// The dots of a secret value that is not shown, named for a screen reader.
export default function Masked(props: Omit<BoxProps<'span'>, 'children' | 'component'>) {
  return (
    <Box component="span" role="img" aria-label={MASKED_NAME} {...props}>
      {MASK}
    </Box>
  )
}

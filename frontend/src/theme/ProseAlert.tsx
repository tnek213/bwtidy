import { Alert, type AlertProps } from '@mui/material'
import Prose from './Prose.tsx'

// An alert that holds running text: the text is a Prose in the alert's own color and the box ends after it.
export default function ProseAlert({ children, sx, ...props }: AlertProps) {
  return (
    <Alert {...props} sx={[{ width: 'fit-content', maxWidth: '100%' }, ...(Array.isArray(sx) ? sx : [sx])]}>
      <Prose color="inherit">{children}</Prose>
    </Alert>
  )
}

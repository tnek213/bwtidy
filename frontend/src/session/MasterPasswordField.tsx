import { IconButton, InputAdornment, TextField, Tooltip } from '@mui/material'
import { EyeIcon } from '../shared/EyeIcon.tsx'

interface MasterPasswordFieldProps {
  shown: boolean
  onToggle: () => void
  disabled: boolean
}

// The uncontrolled master password input of the unlock and the Bitwarden login, with a button
// that shows the typed value. The pages hide it again when the form is sent or fails.
export default function MasterPasswordField({ shown, onToggle, disabled }: MasterPasswordFieldProps) {
  const label = shown ? 'Hide master password' : 'Show master password'
  return (
    <TextField
      name="password"
      type={shown ? 'text' : 'password'}
      label="Master password"
      autoComplete="current-password"
      autoFocus
      fullWidth
      required
      disabled={disabled}
      margin="normal"
      slotProps={{
        input: {
          endAdornment: (
            <InputAdornment position="end">
              <Tooltip title={label}>
                <span>
                  <IconButton size="small" edge="end" aria-label={label} disabled={disabled} onClick={onToggle}>
                    <EyeIcon off={shown} />
                  </IconButton>
                </span>
              </Tooltip>
            </InputAdornment>
          ),
        },
      }}
    />
  )
}

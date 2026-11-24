import { SvgIcon, type SvgIconProps } from '@mui/material'

// A clock with an arrow running back, for a change that a later restore undid.
export function RestoredIcon(props: SvgIconProps) {
  return (
    <SvgIcon {...props}>
      <path d="M13 3a9 9 0 0 0-9 9H1l3.9 3.9L8.8 12H6a7 7 0 1 1 2.05 4.95l-1.42 1.42A9 9 0 1 0 13 3m-1 5v5l4.25 2.52.77-1.28-3.52-2.09V8z" />
    </SvgIcon>
  )
}

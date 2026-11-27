import { Button, type ButtonProps } from '@mui/material'

// The class of the nav button that opens the current page, for tests and for the one style below.
export const CURRENT_NAV_CLASS = 'nav-button-current'

const BASE_SX = {
  // Full height of the bar, so the mark sits at its bottom edge.
  alignSelf: 'stretch',
  borderRadius: 0,
  position: 'relative',
  // The mark is always there, transparent unless current, so a page change shifts nothing.
  '&::after': {
    content: '""',
    position: 'absolute',
    left: 0,
    right: 0,
    bottom: 0,
    height: 3,
    borderRadius: '3px 3px 0 0',
    backgroundColor: 'currentColor',
    opacity: 0,
  },
  [`&.${CURRENT_NAV_CLASS}`]: {
    cursor: 'default',
    '&:hover': { backgroundColor: 'transparent' },
    '&::after': { opacity: 1 },
  },
} as const

interface NavButtonProps extends Omit<ButtonProps, 'color' | 'disabled'> {
  current: boolean
}

// A page button of the app bar. The one of the open page shows a bar under it and does nothing
// when clicked. It is not disabled, so the label keeps its color and a screen reader announces it.
export default function NavButton({ current, onClick, className, ...rest }: NavButtonProps) {
  return (
    <Button
      {...rest}
      color="inherit"
      className={current ? `${className ?? ''} ${CURRENT_NAV_CLASS}`.trim() : className}
      aria-current={current ? 'page' : undefined}
      disableRipple={current || rest.disableRipple}
      onClick={current ? undefined : onClick}
      sx={BASE_SX}
    />
  )
}

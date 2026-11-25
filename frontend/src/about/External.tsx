import { Link } from '@mui/material'
import type { ReactNode } from 'react'

// A link that opens in a new tab, without giving the page there a handle on this one.
export default function External({ href, children }: { href: string; children: ReactNode }) {
  return (
    <Link href={href} target="_blank" rel="noopener noreferrer">
      {children}
    </Link>
  )
}

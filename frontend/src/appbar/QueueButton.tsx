import { Badge, Tooltip } from '@mui/material'
import { commitText, isUiEntry, useAutoCommit } from '../commit/autoCommit.ts'
import { useQueue } from '../api/queue.ts'
import NavButton from './NavButton.tsx'
import useNow from './useNow.ts'

interface QueueButtonProps {
  current: boolean
  onClick: () => void
}

// The Queue link with a badge counting every pending entry. Its tooltip says what the number is made of.
export default function QueueButton({ current, onClick }: QueueButtonProps) {
  const pending = useQueue('pending').data ?? []
  const { status } = useAutoCommit()
  const unsaved = pending.filter(isUiEntry).length
  const proposals = pending.length - unsaved
  const now = useNow(unsaved > 0)

  const lines: string[] = []
  if (unsaved > 0) {
    lines.push(`${unsaved} unsaved edits. ${commitText(status, now)}`)
  }
  if (proposals > 0) {
    lines.push(`${proposals} MCP proposals to review.`)
  }
  if (lines.length === 0) {
    lines.push('Nothing pending.')
  }
  const counts = [
    ...(unsaved > 0 ? [`${unsaved} unsaved edits`] : []),
    ...(proposals > 0 ? [`${proposals} MCP proposals to review`] : []),
  ]

  return (
    <Tooltip
      describeChild
      title={
        <>
          {lines.map((line) => (
            <div key={line}>{line}</div>
          ))}
        </>
      }
    >
      <NavButton
        current={current}
        aria-label={counts.length > 0 ? `Queue, ${counts.join(', ')}` : 'Queue, nothing pending'}
        onClick={onClick}
      >
        <Badge badgeContent={pending.length} color="secondary" max={999} sx={{ '& .MuiBadge-badge': { right: -12 } }}>
          Queue
        </Badge>
      </NavButton>
    </Tooltip>
  )
}

import { Badge, Tooltip } from '@mui/material'
import { commitText, isUiEntry, useAutoCommit } from '../commit/autoCommit.ts'
import { useQueue } from '../api/queue.ts'
import NavButton from './NavButton.tsx'
import useNow from './useNow.ts'

interface QueueButtonProps {
  current: boolean
  onClick: () => void
}

// The name holds the badge's number, which is the sum when both kinds wait (WCAG 2.5.3, label in name).
function queueLabel(total: number, counts: string[]): string {
  if (counts.length === 0) {
    return 'Queue, nothing pending'
  }
  return counts.length === 1 ? `Queue, ${counts[0]}` : `Queue, ${total} pending: ${counts.join(', ')}`
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
        aria-label={queueLabel(pending.length, counts)}
        onClick={onClick}
      >
        {/* An empty queue has no badge: a hidden badge still holds a 0, which would be text the name lacks. */}
        {pending.length > 0 ? (
          <Badge badgeContent={pending.length} color="secondary" max={999} sx={{ '& .MuiBadge-badge': { right: -12 } }}>
            {/* The space keeps the word apart from the badge's number in the text a screen reader compares with the name. */}
            Queue{' '}
          </Badge>
        ) : (
          'Queue'
        )}
      </NavButton>
    </Tooltip>
  )
}

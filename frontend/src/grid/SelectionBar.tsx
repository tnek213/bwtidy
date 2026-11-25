import { Button, Paper, Typography } from '@mui/material'

// Shown right below the grid while any row is ticked.
export default function SelectionBar({
  count,
  onTrash,
  onClear,
}: {
  count: number
  onTrash: () => void
  onClear: () => void
}) {
  return (
    <Paper
      variant="outlined"
      role="toolbar"
      aria-label="Selected items"
      sx={{ display: 'flex', alignItems: 'center', gap: 2, px: 2, py: 0.5, mt: 0.5 }}
    >
      <Typography role="status" variant="body2">
        {count} selected
      </Typography>
      <Button size="small" color="error" onClick={onTrash}>
        Move to trash
      </Button>
      <Button size="small" onClick={onClear}>
        Clear
      </Button>
    </Paper>
  )
}

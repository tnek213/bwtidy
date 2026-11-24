import { AdapterDateFns } from '@mui/x-date-pickers/AdapterDateFns'
import { DatePicker } from '@mui/x-date-pickers/DatePicker'
import { LocalizationProvider } from '@mui/x-date-pickers/LocalizationProvider'
import { format as formatDay, isValid } from 'date-fns'
import { useState } from 'react'
import { NO_PASSWORD_MANAGER } from './noPasswordManager.ts'
import { datePattern } from './format.ts'
import { useDateFormat } from './DateFormat.tsx'

// A local day as YYYY-MM-DD, null for '' or no such day.
function toDate(day: string): Date | null {
  const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(day)
  if (!match) {
    return null
  }
  const date = new Date(Number(match[1]), Number(match[2]) - 1, Number(match[3]))
  return isValid(date) ? date : null
}

interface DateFieldProps {
  label: string
  // YYYY-MM-DD, '' for no day.
  value: string
  onChange: (value: string) => void
}

// A day typed or picked in the chosen date format (docs/spec.md, Settings), held as YYYY-MM-DD.
export default function DateField({ label, value, onChange }: DateFieldProps) {
  const format = useDateFormat()
  // What the field holds, a half typed day too, which the value cannot hold. It follows the value
  // when the value changes from outside, such as Clear filters.
  const [shown, setShown] = useState({ value, date: toDate(value) })
  if (shown.value !== value) {
    setShown({ value, date: toDate(value) })
  }
  return (
    <LocalizationProvider dateAdapter={AdapterDateFns}>
      <DatePicker
        label={label}
        format={datePattern(format)}
        value={shown.date}
        onChange={(date, context) => {
          if (date === null) {
            setShown({ value: '', date: null })
            onChange('')
          } else if (isValid(date) && context.validationError === null) {
            const day = formatDay(date, 'yyyy-MM-dd')
            setShown({ value: day, date })
            onChange(day)
          } else {
            setShown({ value, date })
          }
        }}
        slotProps={{
          textField: {
            size: 'small',
            sx: { width: 170 },
            slotProps: { inputLabel: { shrink: true }, htmlInput: NO_PASSWORD_MANAGER },
          },
          // The DatePicker does not pass shouldRespectLeadingZeros on to its field.
          field: { clearable: true, shouldRespectLeadingZeros: true },
        }}
      />
    </LocalizationProvider>
  )
}

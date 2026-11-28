import {
  Alert,
  Box,
  Button,
  Checkbox,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  FormControlLabel,
  IconButton,
  InputAdornment,
  Stack,
  SvgIcon,
  TextField,
  Tooltip,
  Typography,
} from '@mui/material'
import { type ChangeEvent, type FormEvent, useId, useLayoutEffect, useRef, useState } from 'react'
import { useDebouncedRequest } from '../shared/useDebouncedRequest.ts'
import {
  type CheckAnswer,
  type CheckFacts,
  checkPattern,
  type CheckRequest,
  type DatatypeDraft,
  type FormatResult,
  type PatternProblem,
} from '../api/datatypes.ts'
import MarkedLines, { type LineMark } from './MarkedLines.tsx'
import { NO_PASSWORD_MANAGER } from '../shared/noPasswordManager.ts'
import { isFullMatch, MAX_EXAMPLES, resultText, typedPattern, valueLines } from './patterns.ts'
import ProseAlert from '../theme/ProseAlert.tsx'
import { caseFold } from '../shared/caseFold.ts'

const CHECK_DELAY_MS = 250

// What came back for one request, with the values it was about: the lists on
// screen may be ahead of it while the next check waits for its debounce.
type Checked = { sent: CheckRequest; answer: CheckAnswer } | { sent: CheckRequest; failed: string }

// The latest check of what is typed.
function useLiveCheck(request: CheckRequest, wanted: boolean): Checked | null {
  const check = useDebouncedRequest(wanted ? JSON.stringify(request) : null, () => checkPattern(request), CHECK_DELAY_MS)
  if (!wanted || check.forKey === null) {
    return null
  }
  const sent = JSON.parse(check.forKey) as CheckRequest
  if ('error' in check) {
    return { sent, failed: check.error instanceof Error ? check.error.message : String(check.error) }
  }
  return { sent, answer: check.value }
}

// Everything but the Try box's samples, so typing there never disables Save.
function sameRules(left: CheckRequest, right: CheckRequest): boolean {
  const rules = ({ pattern, ignoreCase, wholeValue, examples, counterExamples }: CheckRequest) =>
    JSON.stringify([pattern, ignoreCase, wholeValue, examples, counterExamples])
  return rules(left) === rules(right)
}

// A value's result depends on the pattern and its flags only, not on the box it is in.
function samePattern(left: CheckRequest, right: CheckRequest): boolean {
  return left.pattern === right.pattern && left.ignoreCase === right.ignoreCase && left.wholeValue === right.wholeValue
}

// Every value of an answer with its result, to mark a line whose value was checked already.
function resultsByValue(sent: CheckRequest, facts: CheckFacts): Map<string, FormatResult> {
  const known = new Map<string, FormatResult>()
  const add = (values: string[], results: FormatResult[]) => values.forEach((value, i) => known.set(value, results[i]))
  add(sent.examples, facts.examples)
  add(sent.counterExamples, facts.counterExamples)
  add(sent.samples, facts.samples)
  return known
}

type LineBox = 'examples' | 'counterExamples' | 'samples'

// What a full match and a miss mean in each box: an example must match fully, a counter-example
// must not, a Try line is never wrong.
const MARK_LABELS: Record<LineBox, { full: [boolean, string]; miss: [boolean, string] }> = {
  examples: { full: [false, 'matches'], miss: [true, 'wrong: does not match but is an example'] },
  counterExamples: { full: [true, 'wrong: matches but is a counter-example'], miss: [false, 'does not match'] },
  samples: { full: [false, 'matches'], miss: [false, 'does not match'] },
}

function lineMark(box: LineBox, result: FormatResult | undefined, current: boolean, patternError: boolean): LineMark {
  if (result === undefined || !current) {
    // A value checked with another pattern keeps that answer's symbol, in grey, until the new one comes.
    const symbol = result === undefined ? '•' : isFullMatch(result) ? '✓' : '✗'
    const details = patternError ? 'Not checked: the regular expression has an error.' : 'Not checked yet.'
    return { kind: 'pending', symbol, label: 'not checked yet', details }
  }
  const full = isFullMatch(result)
  const [wrong, label] = full ? MARK_LABELS[box].full : MARK_LABELS[box].miss
  return { kind: wrong ? 'wrong' : 'right', symbol: full ? '✓' : '✗', label, details: resultText(result) }
}

function HelpIcon() {
  return (
    <SvgIcon fontSize="inherit">
      <path d="M11 18h2v-2h-2v2zm1-16C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2zm0 18c-4.41 0-8-3.59-8-8s3.59-8 8-8 8 3.59 8 8-3.59 8-8 8zm0-14c-2.21 0-4 1.79-4 4h2c0-1.1.9-2 2-2s2 .9 2 2c0 2-3 1.75-3 5h2c0-2.25 3-2.5 3-5 0-2.21-1.79-4-4-4z" />
    </SvgIcon>
  )
}

// The syntax of patterns.py (its docstring), which is less than a full regular expression engine takes.
const SYNTAX: [string, string][] = [
  ['a \\.', 'a character as itself, a backslash before one that is not a letter or digit'],
  ['\\n \\t \\r \\f \\v', 'a newline, a tab, a carriage return, a form feed, a vertical tab'],
  ['.', 'any character except a newline'],
  ['[abc] [a-z] [^...]', 'classes, a ] first in a class is a literal'],
  ['\\d \\w \\s', 'a digit, a word character, whitespace (ASCII), also inside classes'],
  ['\\D \\W \\S', 'their negations, outside classes only'],
  ['( ) (?: )', 'groups, none of them capture'],
  ['|', 'alternation'],
  ['* + ? {n} {n,} {n,m}', 'quantifiers'],
  ['^ first, $ last', 'the match starts at the start of the value or ends at its end'],
]

function SyntaxHelp() {
  return (
    <Box sx={{ p: 0.5 }}>
      <Box component="table" sx={{ borderCollapse: 'collapse', '& td': { verticalAlign: 'top', py: 0.25, pr: 1 } }}>
        <tbody>
          {SYNTAX.map(([syntax, meaning]) => (
            <tr key={syntax}>
              <Box component="td" sx={{ fontFamily: 'monospace', whiteSpace: 'nowrap' }}>
                {syntax}
              </Box>
              <td>{meaning}</td>
            </tr>
          ))}
        </tbody>
      </Box>
      <Typography variant="body2" sx={{ mt: 1, fontSize: 'inherit' }}>
        Not supported: lookarounds, backreferences, lazy or possessive quantifiers and anchors in the middle.
      </Typography>
    </Box>
  )
}

function PatternHelper() {
  return (
    <Box component="span" sx={{ display: 'inline-flex', alignItems: 'center', gap: 0.5 }}>
      <span>
        Supports <Box component="code" sx={{ fontFamily: 'monospace' }}>{'. [ ] \\d \\w \\s ( | ) * + ? {n,m}'}</Box>
      </span>
      <Tooltip title={<SyntaxHelp />} describeChild slotProps={{ tooltip: { sx: { maxWidth: 480 } } }}>
        <IconButton size="small" aria-label="Regular expression syntax" sx={{ p: 0.25, fontSize: '1rem' }}>
          <HelpIcon />
        </IconButton>
      </Tooltip>
    </Box>
  )
}

function PatternError({ pattern, problem }: { pattern: string; problem: PatternProblem }) {
  return (
    <Box role="alert" aria-label="Pattern error" sx={{ color: 'error.main' }}>
      {problem.column !== null && (
        <Box
          component="pre"
          data-testid="pattern-caret"
          sx={{ fontFamily: 'monospace', fontSize: '0.875rem', m: 0, overflowX: 'auto' }}
        >
          {`${pattern}\n${' '.repeat(problem.column - 1)}^`}
        </Box>
      )}
      <Typography variant="body2">
        {problem.message}
        {problem.column !== null && ` (column ${problem.column})`}
      </Typography>
    </Box>
  )
}

// The fixed ^ and $ around the pattern of a whole value match, dimmer than what is typed.
function Anchor({ position, text }: { position: 'start' | 'end'; text: string }) {
  return (
    <InputAdornment
      position={position}
      disableTypography
      aria-hidden
      data-testid={`pattern-${position}-anchor`}
      sx={{ m: 0, fontFamily: 'monospace', color: 'text.disabled', userSelect: 'none' }}
    >
      {text}
    </InputAdornment>
  )
}

const exampleIsWrong = (result: FormatResult) => !isFullMatch(result)
const counterExampleIsWrong = (result: FormatResult) => isFullMatch(result)
// The Try box takes as many lines as the examples.
const MAX_SAMPLES = MAX_EXAMPLES

interface Props {
  initial: DatatypeDraft
  title: string
  // The other datatypes' names, which this one cannot take.
  otherNames: string[]
  error: string | null
  onClose: () => void
  onSave: (datatype: DatatypeDraft) => void
}

export default function DatatypeEditor({ initial, title, otherNames, error, onClose, onSave }: Props) {
  const [name, setName] = useState(initial.name)
  // A pattern stored with a ^ first and a $ last opens without them, as a whole value match.
  const [opened] = useState(() => typedPattern(initial.pattern, initial.wholeValue))
  const [pattern, setPattern] = useState(opened.pattern)
  const [wholeValue, setWholeValue] = useState(opened.wholeValue)
  const [description, setDescription] = useState(initial.description)
  const [ignoreCase, setIgnoreCase] = useState(initial.ignoreCase)
  const patternInput = useRef<HTMLInputElement>(null)
  // Where the caret goes once a typed anchor is taken out of the field.
  const [caret, setCaret] = useState<{ at: number } | null>(null)
  useLayoutEffect(() => {
    if (caret !== null) {
      patternInput.current?.setSelectionRange(caret.at, caret.at)
    }
  }, [caret])
  const [examplesText, setExamplesText] = useState(initial.examples.join('\n'))
  const [counterExamplesText, setCounterExamplesText] = useState(initial.counterExamples.join('\n'))
  const [samplesText, setSamplesText] = useState('')
  const titleId = useId()

  const examples = valueLines(examplesText)
  const counterExamples = valueLines(counterExamplesText)
  const tooManyExamples = examples.length > MAX_EXAMPLES
  const tooManyCounterExamples = counterExamples.length > MAX_EXAMPLES
  const samples = valueLines(samplesText)
  const tooManySamples = samples.length > MAX_SAMPLES
  const request: CheckRequest = {
    pattern,
    ignoreCase,
    wholeValue,
    examples,
    counterExamples,
    samples,
  }
  // The backend answers an empty pattern with an error, which a new datatype should not open with.
  const checked = useLiveCheck(request, pattern !== '' && !tooManyExamples && !tooManyCounterExamples && !tooManySamples)
  const answer = checked !== null && 'answer' in checked ? checked.answer : null
  const problem = answer !== null && 'error' in answer ? answer.error : null
  const facts = answer !== null && !('error' in answer) ? answer : null

  const trimmed = name.trim()
  const nameTaken = otherNames.some((other) => caseFold(other.trim()) === caseFold(trimmed))
  const upToDate = checked !== null && sameRules(checked.sent, request)
  const rulesHold =
    facts !== null &&
    upToDate &&
    !facts.examples.some(exampleIsWrong) &&
    !facts.counterExamples.some(counterExampleIsWrong)
  const canSave = trimmed !== '' && !nameTaken && rulesHold

  const known = facts !== null && checked !== null ? resultsByValue(checked.sent, facts) : new Map<string, FormatResult>()
  const current = facts !== null && checked !== null && samePattern(checked.sent, request)
  const markIn = (box: LineBox) => (line: string) => lineMark(box, known.get(line), current, problem !== null)

  function submit(event: FormEvent) {
    event.preventDefault()
    if (canSave) {
      onSave({ ...initial, name: trimmed, pattern, description, ignoreCase, wholeValue, examples, counterExamples })
    }
  }

  function typePattern(event: ChangeEvent<HTMLInputElement>) {
    const typed = typedPattern(event.target.value, wholeValue)
    setPattern(typed.pattern)
    setWholeValue(typed.wholeValue)
    if (typed.pattern !== event.target.value) {
      const at = Math.max(0, (event.target.selectionStart ?? event.target.value.length) - typed.removedAtStart)
      setCaret({ at: Math.min(at, typed.pattern.length) })
    }
  }

  function tickWholeValue(event: ChangeEvent<HTMLInputElement>) {
    if (event.target.checked) {
      setPattern(typedPattern(pattern, true).pattern)
    }
    setWholeValue(event.target.checked)
  }

  return (
    <Dialog open onClose={onClose} aria-labelledby={titleId} fullWidth maxWidth="md">
      <form onSubmit={submit} noValidate>
        <DialogTitle id={titleId}>{title}</DialogTitle>
        <DialogContent>
          <Stack spacing={2} sx={{ pt: 1 }}>
            <TextField
              autoFocus
              fullWidth
              label="Name"
              value={name}
              error={nameTaken}
              helperText={nameTaken ? `There is already a datatype named ${trimmed}.` : undefined}
              onChange={(event) => setName(event.target.value)}
              slotProps={{ htmlInput: NO_PASSWORD_MANAGER }}
            />
            <Box>
              <TextField
                fullWidth
                label="Regular expression"
                value={pattern}
                error={problem !== null}
                helperText={<PatternHelper />}
                onChange={typePattern}
                inputRef={patternInput}
                slotProps={{
                  input: wholeValue
                    ? {
                        startAdornment: <Anchor position="start" text="^" />,
                        endAdornment: <Anchor position="end" text="$" />,
                      }
                    : undefined,
                  htmlInput: { ...NO_PASSWORD_MANAGER, spellCheck: false, sx: { fontFamily: 'monospace' } },
                }}
              />
              <Stack direction="row" spacing={2}>
                <FormControlLabel
                  control={<Checkbox checked={wholeValue} onChange={tickWholeValue} />}
                  label="Whole value"
                />
                <FormControlLabel
                  control={<Checkbox checked={ignoreCase} onChange={(event) => setIgnoreCase(event.target.checked)} />}
                  label="Ignore case"
                />
              </Stack>
              {problem !== null && <PatternError pattern={checked?.sent.pattern ?? pattern} problem={problem} />}
              {facts !== null && (
                <Typography variant="body2" color="text.secondary" aria-label="Pattern facts" sx={{ mt: 0.5 }}>
                  {facts.states} states, largest repeat {facts.maxRepeat}, shortest match {facts.shortest}
                </Typography>
              )}
              {checked !== null && 'failed' in checked && (
                <Alert severity="error" sx={{ mt: 1 }}>
                  Could not check the pattern: {checked.failed}
                </Alert>
              )}
            </Box>
            <TextField
              fullWidth
              label="Description"
              value={description}
              onChange={(event) => setDescription(event.target.value)}
              slotProps={{ htmlInput: NO_PASSWORD_MANAGER }}
            />
            <ProseAlert severity="warning">
              An agent connected through MCP can read the pattern, the examples and the counter-examples of a datatype
              that a non-secret column uses. Never type a real value, use made-up ones.
            </ProseAlert>
            <Stack direction="row" spacing={2} sx={{ alignItems: 'flex-start' }}>
              <Box sx={{ flex: 1, minWidth: 0 }}>
                <MarkedLines
                  label="Examples"
                  value={examplesText}
                  onChange={setExamplesText}
                  error={tooManyExamples}
                  helperText={tooManyExamples ? `At most ${MAX_EXAMPLES} examples.` : 'One per line. Each must match the format.'}
                  markOf={markIn('examples')}
                />
              </Box>
              <Box sx={{ flex: 1, minWidth: 0 }}>
                <MarkedLines
                  label="Counter-examples"
                  value={counterExamplesText}
                  onChange={setCounterExamplesText}
                  error={tooManyCounterExamples}
                  helperText={
                    tooManyCounterExamples
                      ? `At most ${MAX_EXAMPLES} counter-examples.`
                      : 'One per line. None may match the format fully.'
                  }
                  markOf={markIn('counterExamples')}
                />
              </Box>
            </Stack>
            <MarkedLines
              label="Try"
              value={samplesText}
              onChange={setSamplesText}
              error={tooManySamples}
              helperText={tooManySamples ? `At most ${MAX_SAMPLES} lines.` : 'One per line. Not saved.'}
              markOf={markIn('samples')}
            />
            {error !== null && <Alert severity="error">{error}</Alert>}
          </Stack>
        </DialogContent>
        <DialogActions>
          <Button onClick={onClose}>Cancel</Button>
          <Button type="submit" variant="contained" disabled={!canSave}>
            Save
          </Button>
        </DialogActions>
      </form>
    </Dialog>
  )
}

// The dotless i, which Python's casefold keeps while its upper case I would fold to i.
const DOTLESS_I = 'ı'

// A text folded for a comparison that ignores case, as close to Python's str.casefold (the
// backend's rule for names) as JavaScript allows. Lower, upper and lower case again, one code
// point at a time, so ß, ẞ and SS all fold to ss and a final sigma folds like any other.
export function caseFold(text: string): string {
  return Array.from(text, (char) => (char === DOTLESS_I ? char : char.toLowerCase().toUpperCase().toLowerCase())).join('')
}

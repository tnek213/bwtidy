// Where the lines of a paragraph start, chosen for the whole paragraph so that every line but the last comes
// out close in length (minimum raggedness, the idea of Knuth and Plass). widths holds the width of each word,
// space the width of a space and max the width of a line. Returns the index of each word that starts a new line.
// A line costs the square of its slack and the last line costs nothing. A line wider than max is allowed only
// when it is a single word.
export function lineBreaks(widths: number[], space: number, max: number): number[] {
  const count = widths.length
  if (max <= 0 || widths.every((width) => width === 0)) {
    return []
  }
  // cost[end] is the cheapest way to set the words before end, start[end] the first word of its last line.
  const cost = [0]
  const start = [0]
  for (let end = 1; end <= count; end++) {
    cost.push(Infinity)
    start.push(end - 1)
    let width = -space
    for (let first = end - 1; first >= 0; first--) {
      width += widths[first] + space
      if (width > max && first < end - 1) {
        break
      }
      const slack = Math.max(max - width, 0)
      const total = cost[first] + (end === count ? 0 : slack * slack)
      if (total < cost[end]) {
        cost[end] = total
        start[end] = first
      }
    }
  }
  const breaks: number[] = []
  for (let first = start[count]; first > 0; first = start[first]) {
    breaks.unshift(first)
  }
  return breaks
}

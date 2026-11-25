// A copy of the set with the value in it (on) or without it (not on), for a state setter.
export function toggled<T>(set: ReadonlySet<T>, value: T, on: boolean): Set<T> {
  const next = new Set(set)
  if (on) {
    next.add(value)
  } else {
    next.delete(value)
  }
  return next
}

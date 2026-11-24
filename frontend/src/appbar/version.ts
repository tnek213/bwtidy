// The build commit of a version text (`0.1.0-alpha.1 (261116 d475d7f)` gives `d475d7f`), or the whole text without one.
export function shortVersion(version: string): string {
  const match = /\(([^()]*)\)\s*$/.exec(version)
  if (match === null) {
    return version
  }
  return match[1].trim().split(/\s+/).at(-1) ?? version
}

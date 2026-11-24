// Password managers (Bitwarden, 1Password, LastPass, Dashlane and Chrome's own) offer to fill a
// text input and to save what is typed into it. Inside the app every input edits vault data, so
// each one opts out with these attributes and turns autocomplete off. Spread them on an html
// input: on a MUI TextField through slotProps.htmlInput (merged with what the field already sets
// there, such as spellCheck), on an Autocomplete through its renderInput field, on a Select
// through inputProps. The login pages (the account form, the unlock and the Bitwarden login) must
// NOT use this: there password managers are invited to fill and save.
export const NO_PASSWORD_MANAGER = {
  autoComplete: 'off',
  'data-bwignore': 'true',
  'data-1p-ignore': 'true',
  'data-lpignore': 'true',
  'data-form-type': 'other',
} as const

// The same opt-out for an input AG Grid builds itself (agTextCellEditor, agLargeTextCellEditor),
// which takes no React props from us.
function markNoPasswordManager(element: HTMLInputElement | HTMLTextAreaElement): void {
  for (const [name, value] of Object.entries(NO_PASSWORD_MANAGER)) {
    element.setAttribute(name.toLowerCase(), value)
  }
}

const EDITOR_INPUTS =
  '.ag-cell-inline-editing input, .ag-cell-inline-editing textarea, .ag-popup-editor input, .ag-popup-editor textarea'

// Marks the cell editor's input under the grid's root, for the grid's cellEditingStarted event.
// AG Grid dispatches the event before React has rendered the editor, so when no input is there yet
// it watches the root until one shows (or the time is up).
export function markCellEditor(root: HTMLElement, waitMs = 2000): void {
  const mark = () => {
    const inputs = root.querySelectorAll<HTMLInputElement | HTMLTextAreaElement>(EDITOR_INPUTS)
    inputs.forEach(markNoPasswordManager)
    return inputs.length > 0
  }
  if (mark()) {
    return
  }
  const observer = new MutationObserver(() => {
    if (mark()) {
      observer.disconnect()
    }
  })
  observer.observe(root, { childList: true, subtree: true })
  setTimeout(() => observer.disconnect(), waitMs)
}

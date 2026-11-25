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

// Keeps every cell editor input under the grid's root opted out from the moment it is in the page,
// for the grid's root element (a ref callback, it answers the cleanup). AG Grid puts the input of
// its own editors in the page before it dispatches cellEditingStarted, so marking it on that event
// leaves it open to a password manager for a moment. The observer runs right after the change that
// added the input, before any other task, so nothing sees the input unmarked.
export function markCellEditors(root: HTMLElement): () => void {
  const mark = () => {
    root.querySelectorAll<HTMLInputElement | HTMLTextAreaElement>(EDITOR_INPUTS).forEach((input) => {
      if (input.getAttribute('data-bwignore') !== 'true') markNoPasswordManager(input)
    })
  }
  mark()
  const observer = new MutationObserver((records) => {
    if (records.some((record) => record.addedNodes.length > 0)) mark()
  })
  observer.observe(root, { childList: true, subtree: true })
  return () => observer.disconnect()
}

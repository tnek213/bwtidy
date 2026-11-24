interface OperationLabel {
  label: string
  explanation: string
}

// Keyed by the operation kinds of the API and MCP, which stay the vocabulary both the user and the agent see.
export const OPERATION_LABELS: Record<string, OperationLabel> = {
  setLeaf: {
    label: 'Set a value',
    explanation: 'Changed one value of an item: a built-in field such as the username, or a custom field.',
  },
  removeLeaf: {
    label: 'Remove a value',
    explanation: 'Removed one value of an item: cleared a built-in field, or deleted a custom field.',
  },
  generateLeaf: {
    label: 'Generate a value',
    explanation:
      "Replaced a login password or a hidden field with a value from bw's generator, which nobody saw. The old value stays in the item's password history.",
  },
  moveToFolder: {
    label: 'Move to folder',
    explanation: 'Moved items into a folder (created when it was missing), or out of every folder.',
  },
  setCollections: {
    label: 'Set collections',
    explanation: 'Changed which collections of its organization an item is in.',
  },
  moveToOrganization: {
    label: 'Move to organization',
    explanation: 'Moved a personal item into an organization and into the chosen collections.',
  },
  moveToPersonal: {
    label: 'Move to personal vault',
    explanation:
      'Created an organization item anew in the personal vault and moved the old one to the trash (its passkeys and password history are lost).',
  },
  trashItem: {
    label: 'Move to trash',
    explanation: 'Moved an item to the trash. Nothing is ever deleted for good.',
  },
  convertItem: {
    label: 'Change type',
    explanation: 'Created the item anew as another type with its values mapped over, then moved the old item to the trash.',
  },
  applyTemplate: {
    label: 'Apply template',
    explanation:
      "Fitted an item to a template: moved its values into the template's fields and added the missing ones empty, changing the type when needed.",
  },
  renameField: {
    label: 'Rename field',
    explanation: 'Renamed a custom field in every item that has it, keeping its type and position.',
  },
  mergeColumns: {
    label: 'Merge columns',
    explanation: 'Merged two or more columns into one, keeping one value per item and removing the others.',
  },
  mergeItems: {
    label: 'Merge items',
    explanation: 'Moved chosen values from one item into another, then moved the first item to the trash.',
  },
  setUris: {
    label: 'Set URIs',
    explanation:
      "Replaced the URIs of logins, each with its match setting, which decides on which sites autofill offers the login. Default is the client's own setting.",
  },
  restoreItems: {
    label: 'Restore from backup',
    explanation: 'Put the items of an earlier change back as its backup holds them.',
  },
  renameFolder: {
    label: 'Rename folder',
    explanation: 'Gave a folder a new name. Its items stayed in it.',
  },
  deleteFolder: {
    label: 'Delete folder',
    explanation: 'Deleted an empty folder. A folder that holds items is never deleted.',
  },
  restoreFolder: {
    label: 'Restore folder',
    explanation: 'Gave a folder back the name its backup holds, or created it again when it was deleted.',
  },
  renameAttachment: {
    label: 'Rename attachment',
    explanation:
      'Gave an attachment a new file name: bwtidy uploaded the file again under the new name and deleted the old one, so the attachment got a new id.',
  },
  createItem: {
    label: 'Create an item',
    explanation: 'Created a new item from a type or a template, with its secret fields empty or generated.',
  },
  setToolNoteSection: {
    label: "Save bwtidy's settings",
    explanation: 'bwtidy saved a change to one section of its own note: the templates, the datatypes, the to-fill list or the generator settings.',
  },
}

export function operationLabel(kind: string): OperationLabel | undefined {
  return Object.hasOwn(OPERATION_LABELS, kind) ? OPERATION_LABELS[kind] : undefined
}

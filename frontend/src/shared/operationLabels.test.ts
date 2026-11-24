import { expect, test } from 'vitest'
import { OPERATION_LABELS, operationLabel } from './operationLabels.ts'

// Every kind of the Operation union in backend/src/bwtidy/operations.py. A new kind goes here and needs a label.
const BACKEND_KINDS = [
  'setLeaf',
  'removeLeaf',
  'generateLeaf',
  'moveToFolder',
  'setCollections',
  'moveToOrganization',
  'moveToPersonal',
  'trashItem',
  'convertItem',
  'applyTemplate',
  'renameField',
  'mergeColumns',
  'mergeItems',
  'setUris',
  'restoreItems',
  'createItem',
  'renameFolder',
  'deleteFolder',
  'restoreFolder',
  'renameAttachment',
  'setToolNoteSection',
]

test('every operation kind the backend knows has a label and an explanation', () => {
  for (const kind of BACKEND_KINDS) {
    const entry = operationLabel(kind)
    expect(entry?.label, kind).toBeTruthy()
    expect(entry?.explanation, kind).toMatch(/\.$/)
  }
  expect(Object.keys(OPERATION_LABELS).sort()).toEqual([...BACKEND_KINDS].sort())
})

test('labels and explanations keep to the text rules', () => {
  for (const { label, explanation } of Object.values(OPERATION_LABELS)) {
    expect(`${label} ${explanation}`).not.toMatch(/[;—]| -- /)
  }
})

test('an unknown kind has no label', () => {
  expect(operationLabel('somethingNew')).toBeUndefined()
  expect(operationLabel('toString')).toBeUndefined()
})

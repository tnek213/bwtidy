import { FIELDS_PREFIX, fieldColumn } from './coordinates.ts'
import type { FieldType } from '../api/operations.ts'
import type { TemplateCoordinate } from '../api/templates.ts'
import { caseFold } from './caseFold.ts'

// Item type numbers of Bitwarden and the type names the grid uses.
export const ITEM_TYPES: Record<number, string> = { 1: 'login', 2: 'note', 3: 'card', 4: 'identity', 5: 'sshKey' }

export const TYPE_LABELS: Record<string, string> = {
  login: 'Login',
  note: 'Secure note',
  card: 'Card',
  identity: 'Identity',
  sshKey: 'SSH key',
}

export function typeLabel(type: number): string {
  const name = ITEM_TYPES[type]
  return name ? (TYPE_LABELS[name] ?? name) : `Type ${type}`
}

// The built-in slots a template of each type can use. Notes have none.
export const BUILT_IN_SLOTS: Record<number, string[]> = {
  1: ['login.username', 'login.password', 'login.totp', 'login.uris'],
  2: [],
  3: ['card.cardholderName', 'card.brand', 'card.number', 'card.expMonth', 'card.expYear', 'card.code'],
  4: [
    'identity.title',
    'identity.firstName',
    'identity.middleName',
    'identity.lastName',
    'identity.address1',
    'identity.address2',
    'identity.address3',
    'identity.city',
    'identity.state',
    'identity.postalCode',
    'identity.country',
    'identity.company',
    'identity.email',
    'identity.phone',
    'identity.ssn',
    'identity.username',
    'identity.passportNumber',
    'identity.licenseNumber',
  ],
  5: ['sshKey.privateKey', 'sshKey.publicKey', 'sshKey.keyFingerprint'],
}

const SECRET_PATHS = new Set(['login.password', 'login.totp', 'card.number', 'card.code', 'sshKey.privateKey'])

export const FIELD_TYPE_LABELS: Record<FieldType, string> = { 0: 'Text', 1: 'Hidden', 2: 'Boolean' }

// The grid column of a coordinate, fields.user_* for the pattern user_*.
export function coordinateColumn(coordinate: TemplateCoordinate): string {
  if (coordinate.kind === 'path') {
    return coordinate.path
  }
  return coordinate.kind === 'field' ? fieldColumn(coordinate.name) : `${FIELDS_PREFIX}${coordinate.pattern}`
}

// A field name as names are compared: without surrounding spaces, ignoring case (folded_name of
// the backend).
export function foldedName(name: string): string {
  return caseFold(name.trim())
}

export function isSecretCoordinate(coordinate: TemplateCoordinate): boolean {
  return coordinate.kind === 'path' ? SECRET_PATHS.has(coordinate.path) : coordinate.fieldType === 1
}

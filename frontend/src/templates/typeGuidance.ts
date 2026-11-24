// When to pick each item type, shown under the type select of the template editor
// (docs/spec.md, Templates). Keyed like ITEM_TYPES.

interface TypeGuidance {
  useFor: string[]
  notFor: string[]
}

export const TYPE_GUIDANCE: Record<number, TypeGuidance> = {
  1: {
    useFor: [
      'A website or web app you sign in to with a username and password, with its TOTP and URLs.',
      'A device or account you sign in to: a computer user, a router or NAS admin page, a mail or VPN account.',
      'A note that only holds a username and a password: as a login the browser extension copies and fills them.',
    ],
    notFor: [
      'A Wi-Fi network or a disk passphrase without a username: a secure note with fields.',
      'A passport, ID card or driving licence number: an identity.',
    ],
  },
  2: {
    useFor: [
      'A secret that is not a sign-in: a Wi-Fi password, a disk encryption passphrase, a BitLocker recovery key.',
      'A license key, recovery codes or an API token, in hidden custom fields.',
      'Free text such as instructions or answers to security questions.',
    ],
    notFor: [
      'Something you sign in to with a username and password: a login, so the extension can copy and fill it.',
      'A payment card: a card, so its number and expiry have their own slots.',
    ],
  },
  3: {
    useFor: [
      'A credit or debit card: cardholder, number, expiry and security code.',
      'A prepaid or gift card that has a number and a code.',
    ],
    notFor: [
      'The internet bank you sign in to: a login.',
      'A membership or loyalty card without payment details: a secure note.',
    ],
  },
  4: {
    useFor: [
      'A person you fill forms for: name, address, email and phone.',
      'A passport, national ID or driving licence: identity has slots for their numbers.',
    ],
    notFor: [
      'An account you sign in to, even a government one: a login.',
      'A one-off code or document without personal details: a secure note.',
    ],
  },
  5: {
    useFor: [
      'An SSH key pair you use to reach servers or Git hosts: private key, public key and fingerprint.',
      'A deploy key of one repository.',
    ],
    notFor: [
      'The password of the server account: a login.',
      'An API token or a GPG key: a secure note.',
    ],
  },
}

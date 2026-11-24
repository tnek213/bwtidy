"""Items shaped like bw serve's GET /list/object/items, one of every type, all values made up.

SECRETS lists every secret value in them, so tests can check none leaks.
"""

from typing import Any

FOLDERS = [
    {"object": "folder", "id": None, "name": "No Folder"},
    {"object": "folder", "id": "f1", "name": "Work"},
]
ORGANIZATIONS = [{"object": "organization", "id": "o1", "name": "Family"}]
COLLECTIONS = [
    {"object": "collection", "id": "c1", "organizationId": "o1", "name": "Youth"},
    {"object": "collection", "id": "c2", "organizationId": "o1", "name": "shared"},
]

LOGIN: dict[str, Any] = {
    "object": "item",
    "id": "login-1",
    "folderId": "f1",
    "type": 1,
    "reprompt": 0,
    "name": "Example Mail",
    "favorite": True,
    "fields": [
        {"name": "User", "value": "dev", "type": 0, "linkedId": None},
        {"name": "PIN", "value": "pin-secret-4711", "type": 1, "linkedId": None},
        {"name": "Linked", "value": None, "type": 3, "linkedId": 100},
    ],
    "login": {
        "uris": [
            {"match": None, "uri": "https://mail.example.test"},
            {"match": 3, "uri": "https://webmail.example.test"},
        ],
        "username": "dev@mail.example.test",
        "password": "pw-login-secret-e0e7",
        "totp": "otpauth://totp/Mail:dev?secret=TOTPSECRETKRSXG5",
        "passwordRevisionDate": "2026-01-01T00:00:00.000Z",
        "fido2Credentials": [{"credentialId": "cred-1", "keyValue": "passkey-secret-MHcC"}],
    },
    "passwordHistory": [{"lastUsedDate": "2025-11-02T10:15:00.000Z", "password": "old-pw-one"}],
    ## the size is the encrypted blob's and fits what test_collect's fake bw answers for a1
    "attachments": [{"id": "a1", "fileName": "codes.txt", "size": "81", "url": "https://x"}],
    "creationDate": "2026-01-01T00:00:00.000Z",
    "revisionDate": "2026-10-26T10:00:00.000Z",
    "deletedDate": None,
}

NOTE: dict[str, Any] = {
    "object": "item",
    "id": "note-1",
    "organizationId": "o1",
    "collectionIds": ["c2", "c1"],
    "type": 2,
    "reprompt": 1,
    "name": "Backup disk",
    "notes": "Free text, not secret",
    "favorite": False,
    "fields": [
        {"name": "Encryption passphrase ", "value": "luks-secret-9f8e", "type": 1},
        {"name": "dm.crypt.v2", "value": "dotted text", "type": 0},
        {"name": "WPA key", "value": "wpa-main-secret", "type": 1},
        {"name": "WPA key", "value": "wpa-guest-secret", "type": 1},
        {"name": "Empty", "value": "", "type": 0},
        {"name": "Empty", "value": "second empty has a value", "type": 0},
        {"name": "Enabled", "value": "false", "type": 2},
    ],
    "secureNote": {"type": 0},
    "revisionDate": "2026-10-26T11:00:00.000Z",
}

CARD: dict[str, Any] = {
    "object": "item",
    "id": "card-1",
    "type": 3,
    "reprompt": 0,
    "name": "Visa debit",
    "favorite": False,
    "fields": [],
    "card": {
        "cardholderName": "Dev User",
        "brand": "Visa",
        "number": "4111111111111111",
        "expMonth": "12",
        "expYear": "2029",
        "code": "9871",
    },
    "revisionDate": "2026-10-26T12:00:00.000Z",
}

IDENTITY: dict[str, Any] = {
    "object": "item",
    "id": "identity-1",
    "type": 4,
    "reprompt": 0,
    "name": "Me",
    "favorite": False,
    "identity": {
        "title": "Mr",
        "firstName": "Dev",
        "middleName": None,
        "lastName": "User",
        "email": "dev@example.test",
        "ssn": "19700101-0000",
        "address1": "",
    },
    "revisionDate": "2026-10-26T13:00:00.000Z",
}

SSH_KEY: dict[str, Any] = {
    "object": "item",
    "id": "ssh-1",
    "type": 5,
    "reprompt": 0,
    "name": "Server key",
    "favorite": False,
    "sshKey": {
        "privateKey": "-----BEGIN OPENSSH PRIVATE KEY-----\nssh-private-secret\n",
        "publicKey": "ssh-ed25519 AAAAC3Nza dev@host",
        "keyFingerprint": "SHA256:abcdef",
    },
    "revisionDate": "2026-10-26T14:00:00.000Z",
}

ITEMS = [LOGIN, NOTE, CARD, IDENTITY, SSH_KEY]

SECRETS = [
    "pin-secret-4711",
    "pw-login-secret-e0e7",
    "otpauth://totp/Mail:dev?secret=TOTPSECRETKRSXG5",
    "passkey-secret-MHcC",
    "old-pw-one",
    "luks-secret-9f8e",
    "wpa-main-secret",
    "wpa-guest-secret",
    "4111111111111111",
    "9871",
    "-----BEGIN OPENSSH PRIVATE KEY-----\nssh-private-secret\n",
]

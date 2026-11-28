"""Collecting a full backup of the vault through bw (see docs/spec.md, Backups).

A backup is one zip, encrypted (crypto.write_encrypted) and written as <UTC timestamp>.zip.age.
The exports come from the bw CLI with BW_SESSION in its environment, the raw listings from bw
serve. A failed step leaves no .zip.age behind.
"""

import asyncio
import io
import json
import logging
import os
import zipfile
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

from bwtidy.backups.crypto import write_encrypted
from bwtidy.backups.files import BackupError, BackupInfo, FingerprintStore, fingerprint, free_name
from bwtidy.bw import BwClient, BwError, run_cli
from bwtidy.protocol import backup_time
from bwtidy.timing import Timings
from bwtidy.vault import Listing, Objects, list_items_settled, list_until

log = logging.getLogger(__name__)


## one bw call starts Node and may download attachments, a prompt would hang forever
BW_TIMEOUT = 300.0
## how much of bw's stderr an error carries
STDERR_LIMIT = 300

## the Dockerfile copies docs/restore.md here, every backup holds it as RESTORE.md
RESTORE_DOC = Path("/app/docs/restore.md")
RESTORE_MEMBER = "RESTORE.md"
## the line of docs/restore.md that the version is put into
RESTORE_DOC_COMMIT = "Written for bwtidy COMMIT."


class BackupStopped(BackupError):
    """The session's end asked the backup to stop, and it did before its next step."""

    def __init__(self, step: str) -> None:
        super().__init__(step, "stopped, the session ends")


@dataclass(frozen=True)
class Collected:
    """What collect gathered: the zip members, the fingerprint of the listings they hold."""

    members: dict[str, bytes]
    fingerprint: str


class BackupSession(Protocol):
    """What create_backup needs from the login session."""

    @property
    def client(self) -> BwClient: ...

    @property
    def password(self) -> str | None: ...

    @property
    def session_key(self) -> str | None: ...


async def run_bw(
    bw_bin: str, args: list[str], session_key: str, step: str, timeout: float = BW_TIMEOUT
) -> bytes:
    """Run one bw command with the session key in its environment and return its stdout.

    Every failure is a BackupError of the step: bw could not start, ran over the timeout or
    exited non-zero (then with its stderr as the text).
    """
    env = {k: v for k, v in os.environ.items() if k not in ("BW_SESSION", "BW_PASSWORD")}
    env["BW_SESSION"] = session_key
    try:
        code, stdout, stderr = await run_cli(bw_bin, args, env=env, timeout=timeout)
    ## TimeoutError is an OSError, so it goes first
    except TimeoutError:
        raise BackupError(step, f"bw did not finish within {timeout:.0f} s") from None
    except OSError as error:
        raise BackupError(step, f"cannot start bw: {error.strerror or error}") from None
    if code != 0:
        ## bw's own message only, never its stdout, which holds vault content
        message = stderr.decode(errors="replace").strip()[:STDERR_LIMIT] or "no error message"
        raise BackupError(step, f"bw exited with {code}: {message}")
    return stdout


def check_zip(data: bytes, step: str) -> None:
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            bad = archive.testzip()
    except zipfile.BadZipFile:
        raise BackupError(step, "bw did not produce a zip file") from None
    if bad is not None:
        raise BackupError(step, "the zip from bw is damaged")


def check_json(data: bytes, step: str) -> Any:
    try:
        return json.loads(data)
    except ValueError:
        raise BackupError(step, "bw did not produce JSON") from None


def attachment_members(item: dict[str, Any]) -> list[tuple[dict[str, Any], str]]:
    """Every attachment of the item with its member path in the backup zip.

    item-attachments/<itemId>/<fileName>, and a second attachment with the same file name
    on one item gets its id in front, again while that name is taken, so no file is overwritten.
    """
    used: set[str] = set()
    members = []
    for attachment in item.get("attachments") or []:
        file_name = attachment["fileName"]
        while file_name in used:
            file_name = f"{attachment['id']}-{file_name}"
        used.add(file_name)
        members.append((attachment, f"item-attachments/{item['id']}/{file_name}"))
    return members


def listed_size(attachment: dict[str, Any]) -> int | None:
    """The size the listing gives for the attachment (a string), None when it has none."""
    size = attachment.get("size")
    if size is None:
        return None
    try:
        return int(size)
    except (TypeError, ValueError):
        return None


def encrypted_size(length: int) -> int:
    """The size the server lists for an attachment of this many plain bytes.

    The server only knows the encrypted blob: one type byte, a 16 byte IV, a 32 byte HMAC
    and the AES-CBC ciphertext, which PKCS7 pads to the next whole block. Checked on the
    dev vault for lengths from 0 to 1000 bytes.
    """
    return 1 + 16 + 32 + (length // 16 + 1) * 16


def check_attachments(items: Objects, members: dict[str, bytes]) -> None:
    """Every attachment of every item has its member and the member's length fits the listing.

    Raises BackupError("attachments", ...) naming only the item id and the attachment id:
    an attachment's file name can tell what the item is.
    """
    for item in items:
        for attachment, path in attachment_members(item):
            who = f"attachment {attachment['id']} of item {item['id']}"
            data = members.get(path)
            if data is None:
                raise BackupError("attachments", f"{who} is missing from the backup")
            size = listed_size(attachment)
            if size is not None and encrypted_size(len(data)) != size:
                raise BackupError(
                    "attachments",
                    f"{who} has {len(data)} bytes, which does not fit the listed size {size}",
                )


def dump(obj: Any) -> bytes:
    return json.dumps(obj, indent=2, ensure_ascii=False).encode()


def ids(objects: Objects) -> set[str]:
    return {o["id"] for o in objects if o.get("id")}


async def list_covering(listing: Listing, needed: set[str]) -> tuple[Objects, set[str]]:
    """The listing, asked again with the retry budget while it lacks one of the needed ids.

    Returns the listing and the ids it still lacks. Right after a sync bw serve can answer a
    listing that misses what the items refer to, just like the empty item list.
    """
    objects = await list_until(listing, lambda found: needed <= ids(found))
    return objects, needed - ids(objects)


def not_trashed(items: Objects) -> Objects:
    return [i for i in items if not i.get("deletedDate")]


def exported_ids(personal: bytes, orgs: list[Any]) -> set[str]:
    """The ids of the items the personal export's data.json and the organization exports hold."""
    try:
        with zipfile.ZipFile(io.BytesIO(personal)) as archive:
            data = json.loads(archive.read("data.json"))
    except (KeyError, ValueError):
        raise BackupError("personal export", "the zip holds no readable data.json") from None
    found: set[str] = set()
    for export in [data, *orgs]:
        items = export.get("items") if isinstance(export, dict) else None
        found |= {i["id"] for i in items or [] if isinstance(i, dict) and i.get("id")}
    return found


async def get_unlisted(client: BwClient, unlisted: set[str]) -> Objects:
    """Every item an export holds and no listing has, from GET /object/item/<id>.

    bw lists archived items only for an account with premium, while the exports and a get
    by id hold them all. So these are the archived items of an account without premium.
    Anything else is a BackupError: the backup would miss its attachments.
    """
    found = []
    for item_id in sorted(unlisted):
        try:
            item = await client.get_item(item_id)
        except BwError as error:
            raise BackupError("archived items", f"item {item_id}: {error}") from None
        if item.get("id") != item_id or not item.get("archivedDate"):
            raise BackupError(
                "archived items", f"item {item_id} is in an export but in no listing of bw serve"
            )
        found.append(item)
    return found


@dataclass(frozen=True)
class VaultListing:
    """What bw serve lists of the vault for a backup.

    archived holds the archived items, those in the trash too, as every other listing
    leaves them out.
    """

    items: Objects
    archived: Objects
    folders: Objects
    collections: Objects
    organizations: Objects


async def list_vault(client: BwClient) -> VaultListing:
    """Items, archived items, folders, collections and organizations from bw serve, once they
    are settled.

    No items at all, or an item in an organization the listing lacks, is a BackupError: the
    backup would silently miss an export. A folder or collection the listing lacks is only
    a warning, the raw items still carry the ids. The archived items count like the active
    ones here, those in the trash like the trash: not at all.
    """
    items = await list_items_settled(client)
    if not items:
        raise BackupError("vault listing", "bw serve listed no items")
    ## asked after the item list has settled, so bw serve has decrypted the items again
    archived = [*await client.list_archived(), *await client.list_archived(trash=True)]
    placed = [*items, *not_trashed(archived)]
    org_ids = {i["organizationId"] for i in placed if i.get("organizationId")}
    organizations, missing = await list_covering(client.list_organizations, org_ids)
    if missing:
        raise BackupError(
            "vault listing", "an item belongs to an organization that bw serve did not list"
        )
    folder_ids = {i["folderId"] for i in placed if i.get("folderId")}
    folders, missing = await list_covering(client.list_folders, folder_ids)
    if missing:
        affected = sum(1 for i in placed if i.get("folderId") in missing)
        log.warning(
            "Folders missing from bw serve's list: %d, items in them: %d. The backup goes on.",
            len(missing),
            affected,
        )
    collection_ids = {c for i in placed for c in i.get("collectionIds") or []}
    collections, missing = await list_covering(client.list_collections, collection_ids)
    if missing:
        affected = sum(1 for i in placed if missing & set(i.get("collectionIds") or []))
        log.warning(
            "Collections missing from bw serve's list: %d, items in them: %d. The backup goes on.",
            len(missing),
            affected,
        )
    return VaultListing(items, archived, folders, collections, organizations)


async def collect(
    session: BackupSession,
    bw_bin: str,
    session_key: str,
    timeout: float,
    timings: Timings,
    stop: Callable[[], bool] | None = None,
) -> Collected:
    """Every member of the backup zip, as path to bytes, and the fingerprint of the listings.

    stop is asked before every call to bw serve or the CLI: once it answers True the backup
    raises BackupStopped instead of the call, so a running bw is never cut off for it.
    """
    client = session.client

    def check(step: str) -> None:
        if stop is not None and stop():
            raise BackupStopped(step)

    async def bw(step: str, *args: str) -> bytes:
        check(step)
        return await run_bw(bw_bin, list(args), session_key, step, timeout)

    ## bw serve lists an item it created only after a sync, and the server may have changed
    check("sync")
    try:
        await client.sync()
    except BwError as error:
        raise BackupError("sync", str(error)) from None
    timings.lap("sync")
    check("vault listing")
    try:
        vault = await list_vault(client)
    except BwError as error:
        raise BackupError("vault listing", str(error)) from None
    timings.lap("listing")

    members: dict[str, bytes] = {}
    personal = await bw("personal export", "--raw", "export", "--format", "zip")
    check_zip(personal, "personal export")
    members["personal-export.zip"] = personal
    timings.lap("personal export")

    org_exports = []
    for org in vault.organizations:
        org_id = org["id"]
        step = "org export"
        data = await bw(step, "--raw", "export", "--format", "json", "--organizationid", org_id)
        org_exports.append(check_json(data, step))
        members[f"org-{org_id}-export.json"] = data
    timings.lap("org exports")

    trash = await bw("trash listing", "list", "items", "--trash")
    trashed = check_json(trash, "trash listing")
    if not isinstance(trashed, list):
        raise BackupError("trash listing", "bw did not produce a list")
    members["raw/trash.json"] = trash
    timings.lap("trash listing")

    listed = ids([*vault.items, *trashed, *vault.archived])
    unlisted = exported_ids(personal, org_exports) - listed
    if unlisted:
        check("archived items")
    archived = [*vault.archived, *await get_unlisted(client, unlisted)]
    timings.lap("archived items")

    ## one download at a time: several bw processes at once share one CLI state
    every = [*vault.items, *trashed, *archived]
    for item in every:
        for attachment, path in attachment_members(item):
            try:
                members[path] = await bw(
                    "attachments",
                    "--raw",
                    "get",
                    "attachment",
                    attachment["id"],
                    "--itemid",
                    item["id"],
                )
            except BackupError as error:
                raise BackupError(
                    "attachments",
                    f"attachment {attachment['id']} of item {item['id']}: {error.detail}",
                ) from None
    check_attachments(every, members)
    timings.lap("attachments")

    members["raw/items.json"] = dump(vault.items)
    members["raw/archived.json"] = dump(archived)
    members["raw/folders.json"] = dump(vault.folders)
    members["raw/collections.json"] = dump(vault.collections)
    members["raw/organizations.json"] = dump(vault.organizations)
    ## over the listings alone, as the cache sees the vault: with the items get_unlisted found
    ## no backup would ever match the cache. Without premium a client unarchives an item it
    ## edits, so these change only by leaving the archive, which the listings see.
    print_ = fingerprint(
        vault.items,
        vault.folders,
        vault.collections,
        vault.organizations,
        archived=not_trashed(vault.archived),
    )
    return Collected(members, print_)


def restore_manual(path: Path, version: str) -> bytes | None:
    """docs/restore.md with the version the image was built from, None when it cannot be read.

    A backup never fails for its manual, a missing one is only logged.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as error:
        reason = error.strerror if isinstance(error, OSError) else str(error)
        log.warning("The backup holds no %s, %s cannot be read: %s", RESTORE_MEMBER, path, reason)
        return None
    return text.replace(RESTORE_DOC_COMMIT, f"Written for bwtidy {version}.", 1).encode()


def build_zip(members: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for path, data in members.items():
            archive.writestr(path, data)
    return buffer.getvalue()


async def create_backup(
    session: BackupSession,
    directory: Path,
    bw_bin: str = "bw",
    *,
    store: FingerprintStore | None = None,
    now: datetime | None = None,
    timeout: float = BW_TIMEOUT,
    version: str = "unknown",
    restore_doc: Path = RESTORE_DOC,
    stop: Callable[[], bool] | None = None,
) -> BackupInfo:
    """Back up the whole vault into directory. Returns the new file.

    stop ends the backup between its steps with BackupStopped (see collect).

    The zip also holds restore_doc as RESTORE.md, with the version in it, when it can be read.

    The fingerprint goes into the store under the file's name. The retention is not applied
    here: BackupKeeper prunes after each backup, since it knows which backups are in use.
    Raises BackupError naming the failed step. Nothing is written before every member is in
    hand, so a failure leaves no file behind.
    """
    password, session_key = session.password, session.session_key
    if password is None or session_key is None:
        raise BackupError("session", "the vault is locked")
    timings = Timings()
    collected = await collect(session, bw_bin, session_key, timeout, timings, stop)
    manual = restore_manual(restore_doc, version)
    members = collected.members if manual is None else {**collected.members, RESTORE_MEMBER: manual}
    plaintext = build_zip(members)
    timings.lap("zip")
    now = now or datetime.now(UTC)
    name = free_name(directory, now)
    path = await asyncio.to_thread(write_encrypted, directory, name, plaintext, password)
    timings.lap("encrypt and write")
    size = path.stat().st_size
    if store is not None:
        store.set_backup_fingerprint(name, collected.fingerprint)
    log.info("Backup %s written, %d bytes, %.2f s: %s", name, size, timings.total(), timings)
    return BackupInfo(name, size, backup_time(name) or now, collected.fingerprint)

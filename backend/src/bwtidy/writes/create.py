"""The writes that make a new item: createItem, and the conversions that create the new item
from the old one, copy its attachments and trash the old one (convertItem, applyTemplate
across types, moveToPersonal), with the values bw's generator gives (docs/spec.md, Writes).

A generated value is asked of bw serve right before the create or PUT, with the item's setting
or the default from the tool note (note.generator), and goes nowhere but into the item. An SSH
key whose public key or fingerprint is empty gets them from its private key right before its
create (items/ssh_keys.py, through the path's derive). bw serve knows a created item only
after a sync, so one runs before the attachments go up.
"""

from __future__ import annotations

import copy
import logging
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any

from bwtidy.bw import BwError
from bwtidy.items.coordinates import HIDDEN_FIELD, set_leaf
from bwtidy.operations import (
    ApplyTemplate,
    ConvertItem,
    CreateItem,
    GenerateLeaf,
    MoveToPersonal,
)
from bwtidy.sections.generator import bw_query
from bwtidy.vault import list_until, template_named
from bwtidy.writes.folders import folder_id
from bwtidy.writes.note import follow_conversion, generator
from bwtidy.writes.planners import (
    check_generate_leaf,
    generated_coordinates,
    plan_conversion,
    plan_create,
    plan_personal,
    plan_template,
)
from bwtidy.writes.rules import plain_matches_secret

if TYPE_CHECKING:
    from bwtidy.writes.path import Write, WriteContext

log = logging.getLogger(__name__)


async def write_create_item(path: WriteContext, write: Write, op: CreateItem) -> None:
    """Create the item a createItem plans. Generated values are asked of bw serve right
    here, with the default generator settings, and go nowhere but into the new item."""
    body = plan_create(op, path.cache, path.remembered)
    body["folderId"] = await folder_id(path, op.folderName)
    columns = generated_coordinates(op)
    if columns:
        options = (await generator(path)).default_options()
        for coordinate in columns:
            set_leaf(body, coordinate, await path.client.generate(bw_query(options)), HIDDEN_FIELD)
    plain_matches_secret(body, None, path.cache.lookups(), folder=op.folderName)
    write.created.append(await create(path, write, body, []))


async def write_convert_item(path: WriteContext, write: Write, op: ConvertItem) -> None:
    lookups = path.cache.lookups()
    write.created.append(
        await convert(
            path,
            write,
            op.itemId,
            lambda full: plan_conversion(full, op.newType, op.mapping, lookups),
        )
    )
    await follow_conversion(path, write, op.itemId, write.created[-1])


async def write_apply_template(path: WriteContext, write: Write, op: ApplyTemplate) -> None:
    template = template_named(path.cache, path.remembered, op.template)
    lookups = path.cache.lookups()
    full = await path.get_checked(write, op.itemId)
    if full.get("type") != template.type:
        write.created.append(
            await convert(
                path,
                write,
                op.itemId,
                lambda item: plan_template(item, template, op.mapping, lookups),
                full,
            )
        )
        await follow_conversion(path, write, op.itemId, write.created[-1])
        return
    await path.put(write, op.itemId, plan_template(full, template, op.mapping, lookups))


async def write_move_to_personal(path: WriteContext, write: Write, op: MoveToPersonal) -> None:
    write.created.append(await convert(path, write, op.itemId, plan_personal))
    await follow_conversion(path, write, op.itemId, write.created[-1])


async def write_generate_leaf(path: WriteContext, write: Write, op: GenerateLeaf) -> None:
    """Set the leaf to a value from bw's generator, with the item's setting or the default.

    The value goes from bw serve's answer straight into the item's PUT, nowhere else.
    """
    options = (await generator(path)).options_for(op.itemId)
    full = await path.get_checked(write, op.itemId)
    coordinate = check_generate_leaf(full, op.column)
    before = copy.deepcopy(full)
    set_leaf(full, coordinate, await path.client.generate(bw_query(options)), HIDDEN_FIELD)
    plain_matches_secret(full, before, path.cache.lookups())
    await path.put(write, op.itemId, full)


async def convert(
    path: WriteContext,
    write: Write,
    item_id: str,
    plan: Callable[[dict[str, Any]], dict[str, Any]],
    full: dict[str, Any] | None = None,
) -> str:
    """Create the new item planned from the old one, copy the attachments, trash the old.

    Returns the new item's id.
    """
    if full is None:
        full = await path.get_checked(write, item_id)
    attachments = [
        (a["fileName"], _download(path, a["id"], item_id)) for a in full.get("attachments") or []
    ]
    return await create(path, write, plan(full), attachments, trash=item_id)


def _download(
    path: WriteContext, attachment_id: str, item_id: str
) -> Callable[[], Awaitable[bytes]]:
    return lambda: path.client.get_attachment(attachment_id, item_id)


async def create(
    path: WriteContext,
    write: Write,
    body: dict[str, Any],
    attachments: list[tuple[str, Callable[[], Awaitable[bytes]]]],
    trash: str | None = None,
) -> str:
    """Create an item, upload its attachments (file name and how to get the bytes), then
    trash the item named by trash. Returns the new item's id.

    bw serve knows a created item only after a sync, so one runs before the attachments
    go up. Right after that sync bw serve answers "Not found" for every object while it
    decrypts the items again (the same moment it lists no items, see VaultCache), so the
    attachments wait until it lists the new item. When anything after the create fails,
    the new item is trashed again.
    """
    client = path.client
    path.derive(write, body)
    created = await client.create_item(body)
    new_id = created["id"]
    try:
        await path.sync(write.batch)
        await list_until(client.list_items, lambda items: any(i.get("id") == new_id for i in items))
        for file_name, fetch in attachments:
            await client.upload_attachment(new_id, file_name, await fetch())
        if trash is not None:
            await client.trash_item(trash)
    except BaseException:
        try:
            await client.trash_item(new_id)
        except BwError as error:
            log.warning("Could not trash the half-made item %s: %s", new_id, error)
        raise
    return new_id

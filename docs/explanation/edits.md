# How edits are saved

Nothing you do in the grid reaches your vault at once. Every edit goes into
a queue first, whether you change a cell or save a template. A dialog and a
merge queue their edits the same way. bwtidy writes the queue in batches and
takes a backup before each one. This page explains what a write does to your
vault and how you undo one.

## The queue and the auto-commit

An edit shows in the grid at once on an amber background, and the app bar
shows Commit now. The auto-commit writes all waiting edits together 180
seconds after the oldest one or as soon as 20 wait. You change both numbers
in the Auto-commit box on the Queue page. Commit now writes the waiting edits
at once. A save on the Types page or of the generator settings skips the wait
and is written right away.

When you log out or switch account, bwtidy writes the waiting edits first.
The same happens when your session times out. An edit that could not be
written then stays in the queue and is written right after the next unlock.

An agent's proposals wait in the same queue. The auto-commit never takes
them, so they are written only when you approve them (see
[What an agent can do](mcp.md)). The only writes that skip the queue are a
few to bwtidy's own note (see [The bwtidy note](tool-note.md)).

## What a write does

A write first syncs with the server so that bwtidy sees changes made in
other Bitwarden apps. It then checks each edit against the item as it is
now. An edit fails as a conflict when its item changed elsewhere since you
made it, and nothing of that edit is written. The other edits go on without
it. Such a change gives the item a new revision date or changes its folder,
favorite or collections.

Next comes a backup of the whole vault as it is now. When the vault has not
changed since the newest backup, bwtidy reuses that one. Without a backup
nothing is written and the edits stay in the queue. The backup can take a
while, so the write syncs and checks once more after it.

Then the write takes one item at a time. It fetches the item whole and
changes only the values that the edit touches before it puts the item back.
Everything else on the item stays as it was. That includes its passkeys and
password history, its attachments and any field that bwtidy does not know.
Each item gets one last check right before it is written. When an edit fails
there the edits before it stay written and the ones after it wait in the
queue for the next write.

Last, the write records itself in the change log and reloads the vault. It
also starts the next backup in the background.

```mermaid alt="A write syncs and checks the edits, then takes a backup and checks again before it writes the items one at a time and records the changes"
flowchart TD
  queue([Edits in the queue]) --> sync[Sync and check each edit]
  sync -- item changed elsewhere --> conflict[That edit fails as a conflict and the others go on]
  sync --> backup[Take or reuse a backup]
  backup -- no backup --> stay[The edits stay in the queue]
  backup --> again[Sync and check again]
  again -- item changed elsewhere --> conflict
  again --> fetch[Fetch the next item whole and check it]
  fetch -- changed right before --> late[That edit fails and the later ones wait in the queue]
  fetch --> put[Put it back with only the touched values changed]
  put -- more edits --> fetch
  put -- all written --> done[Change log rows, a reload and a backup in the background]
```

While a write runs you can hover the spinner in the app bar to see its step,
such as taking a backup or writing 3 of 5 changes.

## Trash, never delete

Deleting an item moves it to the trash, where any Bitwarden app can restore
it. A change of an item's type creates a new item and moves the old one to
the trash. Taking an item out of an organization does the same. In both
cases the passkeys and password history stay with the old item.

bwtidy deletes only two things for good. One is an empty folder that you
delete. The other is the old file of an attachment you rename, once its copy
under the new name is in place. The backup holds that old file. When such a
rename fails halfway, bwtidy deletes its own copy again as long as the old
file is still there.

## The change log

Every write gets a row in the Change log. The row says when the write ran
and whether it came from the grid or an agent. It names the operation and
the items and fields it touched. It also shows the result and the backup
taken before the write. A row never holds a value.

![The Change log with a row of a notes edit and its Restore button outlined](../images/explanation-change-log.png)

Restore on a row puts its items back as that backup holds them, after a
dialog that says what happens to each one. A restore is a write like any
other. It gets a backup and a row of its own, so you can undo it in turn.
An item deleted for good after the backup comes back as a new item without
its passkeys and password history. [The web UI](../reference/ui.md#change-log)
says what Restore does on each kind of row.

## When a write fails

An edit that fails its checks is not written. A red message names the item
and the reason. The grid marks what changed elsewhere and the Queue page
lists the edit under Failed.

There you can retry the edit or dismiss it. Retry writes it again. When the
item changed elsewhere Retry asks you first. The backup keeps that other
change. Dismiss drops the edit. A failed edit loses its secret value when
bwtidy restarts. You then dismiss it and type the value again.

## For developers

Every edit is a queue entry. It is checked on arrival with the same rules
that the write runs, and one write path commits it after a backup. The
section [How a write flows](../architecture.md#how-a-write-flows) of the
architecture document leads to the code behind this page.

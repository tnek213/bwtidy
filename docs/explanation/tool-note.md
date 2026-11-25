# The bwtidy note

bwtidy keeps its own settings in your vault, in one secure note in your
personal vault (never in an organization, where others would see it), named
`bwtidy` unless you give it another name. So they travel with the vault:
another bwtidy, or this one after a reinstall, finds your templates there,
and every backup holds them.

## What it holds

The note's text is one JSON object with a section per feature:

- `templates`: your templates from the Types page.
- `datatypes`: the formats a template field can require.
- `generator`: the password generator's settings, the default and those of
  single items.
- `toFill`: the To fill in list, the secret fields left empty for you to
  fill in (those a new item or a template left empty, and those you mark),
  with the day each item was added.
- `note`: the name the note should have, `{"name": "..."}`. Without it the
  name is `bwtidy`, so a note from an earlier bwtidy works as it is. See
  [Renaming the note](#renaming-the-note).

Two hidden fields hold the master passwords the backups are encrypted with:
`backup-password` the current one, and `older-backup-passwords` the ones
before it while a re-encryption is unfinished. That is how a login after you
changed your master password can still open the old backups (see
[Logging in](login.md#what-the-master-password-is-used-for)).

## How bwtidy writes it

- The first login creates the note when there is none, and a login updates
  its password fields when the master password changed. These writes go
  straight to `bw serve`, without the queue or a backup, as they run before
  the session starts.
- Every other change saves one section through the queue, like any edit,
  with a backup first. The section is applied to the note as it is at that
  moment, so two saves from two tabs both land. When an item is converted
  to a new one, its generator setting and To fill in entry move with it in
  the same write.
- The To fill in list keeps itself current: whenever bwtidy loads the vault
  it drops the fields that have a value now (filled in bwtidy or any other
  Bitwarden app) and items that are gone, and queues that change.

## How it is protected

- In the grid its row is grey and read-only, and bwtidy refuses every other
  edit or restore of the note. Templates are edited on the Types page.
- bwtidy remembers the note by its id, so a rename loses nothing (see
  [Renaming the note](#renaming-the-note)). Settings, Display can hide its
  row.
- An agent never sees it, not even in a list.
- bwtidy writes the two password fields as hidden fields, whatever type you
  gave them, and the next login makes one you turned into a text field
  hidden again. If you move the note into an organization, the next login
  that has to write them fails and asks you to move it back to your
  personal vault, so your master password never lands in a shared item.

## Renaming the note

You can rename the note in any Bitwarden app. bwtidy still finds it by its
id, so the templates and the other settings stay. The grid then warns that
bwtidy's note is named "NAME" now, with two buttons:

- Rename it back gives the note the name it should have again (`bwtidy`, or
  the name you kept).
- Keep this name makes the new name the one it should have, stored in the
  `note` section, and the warning goes away. It shows again only when the
  note gets yet another name.

Either one writes the note at once, with a backup first, like every write.
A note that kept a new name is also found by a new bwtidy that does not know
its id yet (after a reinstall, say), as its `note` section names it.

## For developers

The note is found by its id, and each section of its notes is one module
saved through the write path. The code behind this page:
[docs/architecture.md, section Who writes each kind of
data](../architecture.md#who-writes-each-kind-of-data).

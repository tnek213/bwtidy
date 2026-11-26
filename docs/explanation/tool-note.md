# The bwtidy note

bwtidy keeps its own settings in your vault. They live in one secure note in
your personal vault, named `bwtidy` unless you give it another name. bwtidy
never keeps it in an organization, where others would see it. So the
settings travel with your vault. Another bwtidy finds your templates there
and so does this one after a reinstall. Every backup holds them as they
were when it was made.

## What it holds

The note's text is one JSON object with a section for each feature:

- `templates` holds your templates from the Types page.
- `datatypes` holds the formats a template field can require.
- `generator` holds the settings of the password generator, both the default
  and those of single items.
- `toFill` holds the To fill in list. It names the secret fields left empty
  for you to fill in, with the day each item was added. A field gets on the
  list when a new item or a template leaves it empty or when you mark it.
- `note` holds the name the note should have, as `{"name": "..."}`. Without
  it the name is `bwtidy`, so a note from an earlier bwtidy works as it is.
  [Renaming the note](#renaming-the-note) tells you more.

Two hidden fields of the note hold the master passwords that the backups are
encrypted with. The field `backup-password` holds the current one. While a
re-encryption is unfinished the field `older-backup-passwords` holds the
ones before it. That is how a login after you changed your master password
can still open the old backups (see
[Logging in](login.md#what-the-master-password-is-used-for)).

## How bwtidy writes it

An unlock creates the note when it finds none. An unlock also writes the
password fields when your master password changed or a field is no longer
hidden. These writes go straight to `bw serve` without the queue or a backup,
because they run before the session starts.

Every other change saves one section through the queue like any edit, with a
backup first. The section is applied to the note as the note is at that
moment, so two saves from two tabs both land. When an item is converted to a
new one its generator setting and its To fill in entry move with it in the
same write. Only the two buttons of [Renaming the note](#renaming-the-note)
skip the queue and they take a backup too.

The To fill in list keeps itself current. Whenever bwtidy loads the vault it
drops the fields that have a value now, whether you filled them in bwtidy or
in any other Bitwarden app. It drops the items that are gone or in the trash
too. When the list changed it queues the new list.

## How it is protected

In the grid the note's row is grey and read-only. bwtidy refuses every other
edit or restore of the note. You edit the templates on the Types page
instead. bwtidy remembers the note by its id, so a rename loses nothing (see
[Renaming the note](#renaming-the-note)). The Display section of Settings
can hide its row. An agent never sees the note and no list it reads names
it.

bwtidy writes the two password fields as hidden fields whatever type you gave
them. If you turn one into a text field the next unlock makes it hidden
again.

Only a note in your personal vault counts as bwtidy's note. If you move it
into an organization the next unlock no longer finds it and creates a new
empty note. A password write that finds the note shared refuses and asks
you to move it back to your personal vault. So bwtidy never writes your
master password into a shared item. Moving the note shares the password it
already holds, so keep it in your personal vault.

## Renaming the note

You can rename the note in any Bitwarden app. bwtidy still finds it by its
id, so your templates and the other settings stay. The grid then warns that
bwtidy's note is named "NAME" now and offers two buttons:

- Rename it back gives the note the name it should have again. That is
  `bwtidy` or the name you kept.
- Keep this name makes the new name the one the note should have. bwtidy
  stores it in the `note` section and the warning goes away. The warning
  shows again only when the note gets yet another name.

Either button writes the note at once with a backup first, like every write.

A note that kept a new name is also found by a bwtidy that does not know its
id yet (after a reinstall, say). Its `note` section names it. Each time
bwtidy looks for its note it tries these ways in turn. Only an unlock
creates a note when none of them finds one.

```graphviz alt="bwtidy looks for its note among your personal secure notes. It first tries the id it remembers, whatever the note is named now. Then it tries the name bwtidy and then a note whose note section holds its own name. The first match is the bwtidy note. When nothing matches an unlock creates a new note named bwtidy. bwtidy remembers the id of the note it settles on."
digraph {
  nodesep=0.4
  ranksep=0.3
  fontname="sans-serif"
  node [fontname="sans-serif" fontsize=13 shape=box style="rounded,filled" fillcolor="#ffffff" color="#5c6bc0" margin="0.12,0.06"]
  edge [fontname="sans-serif" fontsize=12 color="#455a64"]
  start [label="Look among your\npersonal secure notes" fillcolor="#e8eaf6"]
  id [label="A note with the remembered id?"]
  name [label="A note named bwtidy?"]
  own [label="A note whose note section names it?"]
  new [label="An unlock creates a\nnew note named bwtidy" fillcolor="#fff3e0" color="#e65100"]
  found [label="The bwtidy note\nwith its id remembered" fillcolor="#f1f8e9" color="#558b2f"]
  { rank=same; name; found }
  start -> id
  id -> name [label=" no"]
  name -> own [label=" no"]
  own -> new [label=" no"]
  id -> found [label="yes"]
  name -> found [label="yes"]
  own -> found [label="yes"]
  new -> found [constraint=false]
}
```

## For developers

bwtidy finds the note by its id in `vault/tool_note.py`. Each section of its
notes has a module of its own under `sections/` and is saved through the
write path. The code behind this page is in
[docs/architecture.md, section Who writes each kind of data](../architecture.md#who-writes-each-kind-of-data).

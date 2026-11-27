# MCP tools

<!-- Written by scripts/mcp-reference from the tools the MCP server registers.
Do not edit it by hand. Run the script instead. -->

This page lists every tool that bwtidy's MCP server offers an AI agent. It shows
each tool's name, parameters and description as an MCP client sees them. The
agent reads your vault without the secret values and proposes changes to the
queue. It never changes the vault on its own. Nothing it proposes reaches the
vault until you approve it on the Queue page, where you can also reject it.

[What an agent can do](../explanation/mcp.md) explains why it works this way.
[Connect an MCP client](../how-to/mcp.md) shows how to connect your client.

Each tool is one of three kinds:

- A read tool reads the vault or the queue and changes nothing.
- A propose tool adds one entry to the queue. It changes nothing in the vault
  until you approve the entry.
- An end tool ends the bwtidy session like Log out in the web UI. It first
  writes the edits you have waiting in the web UI.

A tool's kind follows its MCP annotations, which the client sees too. A read
tool is marked read-only and an end tool is marked destructive.

The descriptions are the text the agent reads, so they speak to the agent. While
MCP is off a tool call answers only that bwtidy MCP is disabled. [When a request
fails](../how-to/mcp.md#when-a-request-fails) lists the checks that come first
and the answers they give.

## Read tools

### list_items

A read tool.

List the items of the vault, all of them or only those that match every filter
given.

Each item has:

- `id`, `name`, `type`, `folder`, `organization` and `collections`.
- `values`: the other non-secret values by column (`notes`, `login.uris`,
  `login.username`, `fields.<name>`, ...). Its `login.uris` is a list of
  `{uri, match}`, where `match` is a word or null for the client's default (see
  propose_set_uris).
- `attachments`: the id, fileName and size in bytes of each attachment. The size
  is the encrypted size that Bitwarden lists. The file's contents and url are
  never shown.
- `secretColumns`: the secret columns that hold a value.
- `template`: `match`, the name of the template the item matches or null, and
  `scores`, one fill score per template.

A fill score counts the item type as one more thing to match, and each
coordinate's datatype as one more too:

- `total`: the template's required coordinates (the ones not optional), plus 1
  for the type, plus the coordinates with a datatype. An optional coordinate
  with a datatype counts only when it has a value.
- `filled`: the required coordinates with a value, plus 1 when `typeMatches`,
  plus a term from 0 to 1 for each datatype counted in `total`.
- `score`: filled/total. A full match has filled == total, so a value off its
  format makes a near miss.
- `missing`: the required coordinates without a value.

A datatype's term says how well the value matches format, never whether the
value is right. It is 0 without a value. A score with such terms has `formats`,
one entry per non-secret column with a datatype:

- `column`.
- `datatype`: its name.
- `score`: 1 is a full match.
- `distance`: the fewest characters to insert, delete or substitute so that the
  value matches format.
- `firstEdit`: the position in the value of the first of those edits.
- `expected`: what the format wants there.
- `tooLong`: the value is too long to check, so it scores 0.

A secret column never shows a format. With a datatype it counts only as set or
not set, and it has no entry in `formats`.

Secret values are never available, only the names of the secret columns that are
set.

Parameters:

| Parameter | Type | Default | Description |
| --- | --- | --- | --- |
| `text` | string or null | `null` | Case-insensitive text in the name, a file name or any non-secret value |
| `type` | string or null | `null` | login, note, card, identity or sshKey |
| `folder` | string or null | `null` | Folder name, empty for items without a folder |
| `organization` | string or null | `null` | Organization name, empty for items in the personal vault |
| `collection` | string or null | `null` | Collection name |
| `template` | string or null | `null` | Template name the item matches, empty for items matching none |

### get_item

A read tool.

Get one item by its id. It has the same shape as an item of list_items.

Secret values are never available, only the names of the secret columns that are
set.

Parameters:

| Parameter | Type | Default | Description |
| --- | --- | --- | --- |
| `item_id` | string | required | The item id from list_items |

### list_folders

A read tool.

List the folders. Each has its `name`, the number of items in it (`items`) and
the number of items in the trash that are still in it (`itemsInTrash`).
`itemsWithoutFolder` counts the items outside the trash that are in no folder.

Secret values are never available, only the names of the secret columns that are
set.

No parameters.

### list_collections

A read tool.

List the organizations and their collections, with the number of items in each
collection. An organization item needs at least one collection.

Secret values are never available, only the names of the secret columns that are
set.

No parameters.

### list_templates

A read tool.

List the templates. Each has:

- `name`.
- `description`, when the user wrote one.
- The item type.
- `coordinates`. A custom field coordinate carries its field type (0 text, 1
  hidden, 2 boolean). A coordinate with a datatype names it (see
  list_datatypes). An optional one has `"optional": true`.

An item matches a template when it has the template's type and a value for every
coordinate that is not optional, and when each value with a datatype matches
format.

An optional coordinate is not needed for a match and is never in `missing`. A
value it has must still match its datatype.

A coordinate of kind fieldPattern stands for every custom field whose name fits
its pattern and that no other coordinate names. In the pattern `*` matches any
characters. Case and surrounding spaces are ignored. The coordinate counts once
however many fields fit, and its datatype term is the lowest of theirs.

A secret column never shows a format. With a datatype it counts only as set or
not set, and it has no entry in `formats`.

Secret values are never available, only the names of the secret columns that are
set.

No parameters.

### list_datatypes

A read tool.

List the datatypes, the named formats the user defines once and template
coordinates use. Each has:

- `name`, `pattern`, `description` and `ignoreCase`.
- `examples`: values that match format.
- `counterExamples`: values that do not match format.
- `usedBy`: the template coordinates that use it, each with `template` and
  `column`.

The pattern must match the whole value. A datatype with `wholeValue` false may
match any part of the value instead, and there a `^` first or a `$` last still
anchors that side.

A broken datatype counts as no datatype and is not listed. Nor is a datatype
that no non-secret template coordinate uses.

A datatype says how well a value matches format, never that a value is right.
The fill scores of list_items and get_item show the results.

A secret column never shows a format. With a datatype it counts only as set or
not set, and it has no entry in `formats`.

Only the user makes datatypes and corrects values, and no tool proposes either.

Secret values are never available, only the names of the secret columns that are
set.

No parameters.

### list_near_duplicates

A read tool.

List groups of custom field columns whose names are equal after case folding and
trimming whitespace. Each group is a candidate for propose_merge_columns or
propose_rename_field.

Secret values are never available, only the names of the secret columns that are
set.

No parameters.

### list_proposals

A read tool.

List the proposals made through MCP, newest first and at most 100. The web UI's
edits are never listed. Each proposal has:

- `id`: the `entryId` its propose call answered.
- `summary`: the `summary` its propose call answered.
- `status`.
- `createdAt`.
- `decidedAt`: when it left pending, null while pending.
- `reason`.

`status` is one of:

- `pending`: waiting for the user.
- `approved`: being written.
- `rejected`: the user said no. Do not propose it again.
- `applied`: written to the vault.
- `failed`.

`reason` is null except for a failed entry, which has one of these fixed
reasons:

- `conflict`: an item changed since the proposal. List it again and propose
  again.
- `merge conflict`.
- `write failed`.
- `interrupted`.
- `invalid`: no longer fits the vault.
- `failed`.

Secret values are never available, only the names of the secret columns that are
set.

Parameters:

| Parameter | Type | Default | Description |
| --- | --- | --- | --- |
| `status` | `"pending"`, `"approved"`, `"rejected"`, `"applied"`, `"failed"` or null | `null` | Only the proposals with this status |

## Propose tools

### propose_move_to_folder

A propose tool.

Propose moving items into a folder. A folder that does not exist yet is created
with the move. An empty name takes the items out of their folder.

This only proposes the change. It adds one entry to bwtidy's review queue and
changes nothing in the vault. The user approves or rejects it in the bwtidy web
UI. The answer is the entry's `entryId` and `summary`, and list_proposals shows
what became of the entry. Secret values are never available. The user sees every
proposal, and a proposal that would put a secret value into a non-secret column
never reaches the vault.

Parameters:

| Parameter | Type | Default | Description |
| --- | --- | --- | --- |
| `item_ids` | list of string | required | Item ids from list_items |
| `folder` | string | required | Folder name, empty for no folder |
| `reason` | string or null | `null` | Why the change helps, shown to the user with the proposal |

### propose_rename_folder

A propose tool.

Propose a new name for a folder. Its items stay in it. It is refused when the
name is empty or when another folder has it, ignoring case.

This only proposes the change. It adds one entry to bwtidy's review queue and
changes nothing in the vault. The user approves or rejects it in the bwtidy web
UI. The answer is the entry's `entryId` and `summary`, and list_proposals shows
what became of the entry. Secret values are never available. The user sees every
proposal, and a proposal that would put a secret value into a non-secret column
never reaches the vault.

Parameters:

| Parameter | Type | Default | Description |
| --- | --- | --- | --- |
| `folder` | string | required | The folder's name as list_folders names it |
| `new_name` | string | required | The new name |
| `reason` | string or null | `null` | Why the change helps, shown to the user with the proposal |

### propose_delete_folder

A propose tool.

Propose deleting an empty folder. A folder that holds an item is refused, and so
is one whose only items are in the trash. Move its items out first
(propose_move_to_folder). list_folders counts the items of each folder, in the
trash too.

This only proposes the change. It adds one entry to bwtidy's review queue and
changes nothing in the vault. The user approves or rejects it in the bwtidy web
UI. The answer is the entry's `entryId` and `summary`, and list_proposals shows
what became of the entry. Secret values are never available. The user sees every
proposal, and a proposal that would put a secret value into a non-secret column
never reaches the vault.

Parameters:

| Parameter | Type | Default | Description |
| --- | --- | --- | --- |
| `folder` | string | required | The folder's name as list_folders names it |
| `reason` | string or null | `null` | Why the change helps, shown to the user with the proposal |

### propose_set_collections

A propose tool.

Propose the collections of an organization item, replacing the ones it has. A
personal item goes through propose_move_to_organization instead.

This only proposes the change. It adds one entry to bwtidy's review queue and
changes nothing in the vault. The user approves or rejects it in the bwtidy web
UI. The answer is the entry's `entryId` and `summary`, and list_proposals shows
what became of the entry. Secret values are never available. The user sees every
proposal, and a proposal that would put a secret value into a non-secret column
never reaches the vault.

Parameters:

| Parameter | Type | Default | Description |
| --- | --- | --- | --- |
| `item_id` | string | required | The item id from list_items |
| `collections` | list of string | required | Collection names, at least one |
| `reason` | string or null | `null` | Why the change helps, shown to the user with the proposal |

### propose_move_to_organization

A propose tool.

Propose moving a personal item into an organization, in the given collections.
An organization item goes through propose_move_to_personal instead.

This only proposes the change. It adds one entry to bwtidy's review queue and
changes nothing in the vault. The user approves or rejects it in the bwtidy web
UI. The answer is the entry's `entryId` and `summary`, and list_proposals shows
what became of the entry. Secret values are never available. The user sees every
proposal, and a proposal that would put a secret value into a non-secret column
never reaches the vault.

Parameters:

| Parameter | Type | Default | Description |
| --- | --- | --- | --- |
| `item_id` | string | required | The item id from list_items |
| `organization` | string | required | Organization name |
| `collections` | list of string | required | Collection names of that organization, at least one |
| `reason` | string or null | `null` | Why the change helps, shown to the user with the proposal |

### propose_move_to_personal

A propose tool.

Propose moving an organization item into the personal vault.

The item is created anew in the personal vault with its attachments, and the old
one goes to the trash. Its passkeys and password history are lost, and everyone
who shares its collections loses the item.

This only proposes the change. It adds one entry to bwtidy's review queue and
changes nothing in the vault. The user approves or rejects it in the bwtidy web
UI. The answer is the entry's `entryId` and `summary`, and list_proposals shows
what became of the entry. Secret values are never available. The user sees every
proposal, and a proposal that would put a secret value into a non-secret column
never reaches the vault.

Parameters:

| Parameter | Type | Default | Description |
| --- | --- | --- | --- |
| `item_id` | string | required | The item id from list_items |
| `reason` | string or null | `null` | Why the change helps, shown to the user with the proposal |

### propose_rename_item

A propose tool.

Propose a new name for an item.

This only proposes the change. It adds one entry to bwtidy's review queue and
changes nothing in the vault. The user approves or rejects it in the bwtidy web
UI. The answer is the entry's `entryId` and `summary`, and list_proposals shows
what became of the entry. Secret values are never available. The user sees every
proposal, and a proposal that would put a secret value into a non-secret column
never reaches the vault.

Parameters:

| Parameter | Type | Default | Description |
| --- | --- | --- | --- |
| `item_id` | string | required | The item id from list_items |
| `name` | string | required | The new name |
| `reason` | string or null | `null` | Why the change helps, shown to the user with the proposal |

### propose_set_uris

A propose tool.

Propose the whole URI list of a login, each URI with its match setting, in the
order given.

The list replaces the URIs the login has (get_item lists them), so give every
URI to keep. An empty URI or a regex that does not compile is refused at once,
and nothing is queued.

A URI's `match` says when autofill offers the login:

- `domain`: the base domain.
- `host`: the host and port.
- `startsWith`: the start of the URL.
- `exact`: the whole URL.
- `regex`: a regular expression, which Bitwarden evaluates as JavaScript. bwtidy
  checks it with Python and refuses only one that Python cannot compile.
- `never`.
- null is the client's default, a setting of the user's browser extension that
  is not in the vault.

Only URIs and match settings change. No secret value is read or written.

This only proposes the change. It adds one entry to bwtidy's review queue and
changes nothing in the vault. The user approves or rejects it in the bwtidy web
UI. The answer is the entry's `entryId` and `summary`, and list_proposals shows
what became of the entry. Secret values are never available. The user sees every
proposal, and a proposal that would put a secret value into a non-secret column
never reaches the vault.

Parameters:

| Parameter | Type | Default | Description |
| --- | --- | --- | --- |
| `item_id` | string | required | The item id from list_items |
| `uris` | list of `UriInput` | required | The login's new URIs, each {uri, match} |
| `reason` | string or null | `null` | Why the change helps, shown to the user with the proposal |

Each `UriInput`:

| Parameter | Type | Default | Description |
| --- | --- | --- | --- |
| `uri` | string | required | The URI, not empty |
| `match` | `"domain"`, `"host"`, `"startsWith"`, `"exact"`, `"regex"`, `"never"` or null | `null` | domain, host, startsWith, exact, regex, never, or null for default |

### propose_set_uri_match

A propose tool.

Propose one match setting for the URIs of logins. In each item every URI that
`uri_pattern` matches gets the match, and the other URIs keep theirs.

- `uri_pattern` is a shell glob over the whole URI, ignoring case
  (`*example.com*` for every URI holding example.com). Without it every URI
  matches.
- An item whose URIs would all keep their match is left out. When no item is
  left, the call is an error and nothing is queued.
- One queue entry holds every item.

A URI's `match` says when autofill offers the login:

- `domain`: the base domain.
- `host`: the host and port.
- `startsWith`: the start of the URL.
- `exact`: the whole URL.
- `regex`: a regular expression, which Bitwarden evaluates as JavaScript. bwtidy
  checks it with Python and refuses only one that Python cannot compile.
- `never`.
- null is the client's default, a setting of the user's browser extension that
  is not in the vault.

Only URIs and match settings change. No secret value is read or written.

This only proposes the change. It adds one entry to bwtidy's review queue and
changes nothing in the vault. The user approves or rejects it in the bwtidy web
UI. The answer is the entry's `entryId` and `summary`, and list_proposals shows
what became of the entry. Secret values are never available. The user sees every
proposal, and a proposal that would put a secret value into a non-secret column
never reaches the vault.

Parameters:

| Parameter | Type | Default | Description |
| --- | --- | --- | --- |
| `item_ids` | list of string | required | Item ids from list_items |
| `match` | `"domain"`, `"host"`, `"startsWith"`, `"exact"`, `"regex"`, `"never"` or null | required | domain, host, startsWith, exact, regex, never, or null for default |
| `uri_pattern` | string or null | `null` | A shell glob over the whole URI, ignoring case, default every URI |
| `reason` | string or null | `null` | Why the change helps, shown to the user with the proposal |

### propose_rename_field

A propose tool.

Propose renaming a custom field in every item that has it, or only in the items
given. The field keeps its type and position.

An item that already has a field with the new name needs propose_merge_columns
instead.

This only proposes the change. It adds one entry to bwtidy's review queue and
changes nothing in the vault. The user approves or rejects it in the bwtidy web
UI. The answer is the entry's `entryId` and `summary`, and list_proposals shows
what became of the entry. Secret values are never available. The user sees every
proposal, and a proposal that would put a secret value into a non-secret column
never reaches the vault.

Parameters:

| Parameter | Type | Default | Description |
| --- | --- | --- | --- |
| `from_name` | string | required | The field name now, verbatim |
| `to_name` | string | required | The new field name |
| `item_ids` | list of string or null | `null` | Only these items, default every item with the field |
| `reason` | string or null | `null` | Why the change helps, shown to the user with the proposal |

### propose_merge_columns

A propose tool.

Propose merging columns into one target column, in every item that has one of
the source columns or only in the items given.

- Values not kept are removed.
- An item with values in more than one merged column needs a resolution that
  names the column to keep. Without one the call is an error that lists those
  items.
- A built-in target slot must exist in the item's type. A type change goes
  through propose_apply_template.

This only proposes the change. It adds one entry to bwtidy's review queue and
changes nothing in the vault. The user approves or rejects it in the bwtidy web
UI. The answer is the entry's `entryId` and `summary`, and list_proposals shows
what became of the entry. Secret values are never available. The user sees every
proposal, and a proposal that would put a secret value into a non-secret column
never reaches the vault.

Parameters:

| Parameter | Type | Default | Description |
| --- | --- | --- | --- |
| `sources` | list of string | required | Source columns, like fields.User or login.username |
| `target` | string | required | The column that keeps the value |
| `item_ids` | list of string or null | `null` | Only these items, default every item with a source column |
| `resolutions` | object of string or null | `null` | {item id: the merged column whose value to keep} |
| `reason` | string or null | `null` | Why the change helps, shown to the user with the proposal |

### propose_apply_template

A propose tool.

Propose applying a template to an item, with a mapping
`{template column: the item's column}`.

- Mapped values move.
- Unmapped leaves stay.
- Missing template fields are created empty.
- A template of another type converts the item (a new item is created and the
  old one goes to the trash).

This only proposes the change. It adds one entry to bwtidy's review queue and
changes nothing in the vault. The user approves or rejects it in the bwtidy web
UI. The answer is the entry's `entryId` and `summary`, and list_proposals shows
what became of the entry. Secret values are never available. The user sees every
proposal, and a proposal that would put a secret value into a non-secret column
never reaches the vault.

Parameters:

| Parameter | Type | Default | Description |
| --- | --- | --- | --- |
| `item_id` | string | required | The item id from list_items |
| `template` | string | required | Template name from list_templates |
| `mapping` | object of string or null | `null` | {template column: the item's column}, default none |
| `reason` | string or null | `null` | Why the change helps, shown to the user with the proposal |

### propose_convert_item

A propose tool.

Propose converting an item to another type. A new item is created with the new
type and the old one goes to the trash, both after the user approves.

The mapping is `{target column: the item's column}`:

- Mapped columns move. A mapped custom field is dropped from its old place, and
  mapped notes are cleared.
- Unmapped built-in values of the old type become custom fields.
- Attachments are copied.

No value passes through MCP, since the values move inside the vault. A secret
column may not be mapped into a non-secret one.

An SSH key needs its private key mapped from a column of the item, for example
`{"sshKey.privateKey": "notes"}` for a secure note that holds the key. Its
public key and fingerprint are filled in from it when they are empty.

This only proposes the change. It adds one entry to bwtidy's review queue and
changes nothing in the vault. The user approves or rejects it in the bwtidy web
UI. The answer is the entry's `entryId` and `summary`, and list_proposals shows
what became of the entry. Secret values are never available. The user sees every
proposal, and a proposal that would put a secret value into a non-secret column
never reaches the vault.

Parameters:

| Parameter | Type | Default | Description |
| --- | --- | --- | --- |
| `item_id` | string | required | The item id from list_items |
| `type` | string | required | The new type: login, secureNote, card, identity or sshKey |
| `mapping` | object of string or null | `null` | {target column: the item's column}, default none |
| `reason` | string or null | `null` | Why the change helps, shown to the user with the proposal |

### propose_merge_items

A propose tool.

Propose merging one item into another. The mapped columns of the source item
move into the target item (`{target column: source column}`), then the source
item goes to the trash.

A new field gets the type of its source, so a hidden value stays hidden.

This only proposes the change. It adds one entry to bwtidy's review queue and
changes nothing in the vault. The user approves or rejects it in the bwtidy web
UI. The answer is the entry's `entryId` and `summary`, and list_proposals shows
what became of the entry. Secret values are never available. The user sees every
proposal, and a proposal that would put a secret value into a non-secret column
never reaches the vault.

Parameters:

| Parameter | Type | Default | Description |
| --- | --- | --- | --- |
| `from_item_id` | string | required | The item that goes to the trash |
| `to_item_id` | string | required | The item that receives the values |
| `mapping` | object of string | required | {target column: source column}, columns as list_items names them |
| `reason` | string or null | `null` | Why the change helps, shown to the user with the proposal |

### propose_rename_attachment

A propose tool.

Propose a new file name for an attachment of an item. Name the attachment by the
id that list_items and get_item show.

When the user approves, bwtidy renames it itself. It uploads the file again
under the new name and deletes the old one, so the attachment gets a new id. The
contents never pass through MCP, and no tool reads or answers them.

The name is refused when it is empty, holds a slash or a backslash, is the name
the attachment has now or is the name of another attachment of the item
(ignoring case).

This only proposes the change. It adds one entry to bwtidy's review queue and
changes nothing in the vault. The user approves or rejects it in the bwtidy web
UI. The answer is the entry's `entryId` and `summary`, and list_proposals shows
what became of the entry. Secret values are never available. The user sees every
proposal, and a proposal that would put a secret value into a non-secret column
never reaches the vault.

Parameters:

| Parameter | Type | Default | Description |
| --- | --- | --- | --- |
| `item_id` | string | required | The item id from list_items |
| `attachment_id` | string | required | The attachment's id from list_items or get_item |
| `new_name` | string | required | The new file name |
| `reason` | string or null | `null` | Why the change helps, shown to the user with the proposal |

### propose_trash_item

A propose tool.

Propose moving an item to the trash (never a permanent delete).

This only proposes the change. It adds one entry to bwtidy's review queue and
changes nothing in the vault. The user approves or rejects it in the bwtidy web
UI. The answer is the entry's `entryId` and `summary`, and list_proposals shows
what became of the entry. Secret values are never available. The user sees every
proposal, and a proposal that would put a secret value into a non-secret column
never reaches the vault.

Parameters:

| Parameter | Type | Default | Description |
| --- | --- | --- | --- |
| `item_id` | string | required | The item id from list_items |
| `reason` | string or null | `null` | Why the change helps, shown to the user with the proposal |

### propose_create_item

A propose tool.

Propose a new item from a raw type or from a template. Give exactly one of the
two.

- A template brings its type and its custom fields, created empty with their
  field types.
- `values` sets non-secret columns by name, as list_items names them
  (`login.username`, `login.uris`, `notes`, `fields.<name>`, ...). A custom
  field that the template does not name becomes a text field.

Secret values are never taken, and a value for a secret column is refused. A
secret field (password, TOTP, card number or code, private key, hidden field) is
either left blank or generated:

- Left blank: the field starts empty and the user fills it in the bwtidy web UI,
  where it shows up in the To fill in list. When the template requires the
  field, the item shows as near its template.
- Generated: a `login.password` or a hidden field can instead get a value that
  bw generates when the item is written (`generate_secrets`, or
  `generate_password` for a login's password). Nobody sees it until the user
  reveals it in the web UI, and nobody can read it through MCP. It follows the
  default generator settings the user keeps on the Settings page.

An SSH key is refused because its private key cannot come through MCP. Use
propose_convert_item on an item that holds the key instead.

This only proposes the change. It adds one entry to bwtidy's review queue and
changes nothing in the vault. The user approves or rejects it in the bwtidy web
UI. The answer is the entry's `entryId` and `summary`, and list_proposals shows
what became of the entry. Secret values are never available. The user sees every
proposal, and a proposal that would put a secret value into a non-secret column
never reaches the vault.

Parameters:

| Parameter | Type | Default | Description |
| --- | --- | --- | --- |
| `name` | string | required | The new item's name |
| `type` | string or null | `null` | login, secureNote, card or identity, when no template |
| `template` | string or null | `null` | Template name from list_templates, when no type |
| `folder` | string | `""` | Folder name, created when missing, empty for none |
| `organization` | string | `""` | Organization name, empty for the personal vault |
| `collections` | list of string or null | `null` | Collection names of the organization, at least one when an organization is given |
| `values` | object of (string, integer, boolean or list of string) or null | `null` | {column: value} for non-secret columns, default none |
| `generate_password` | boolean | `false` | A login only: bw generates its password at write time, following the default generator settings |
| `generate_secrets` | list of string or null | `null` | Secret columns bw generates at write time, login.password or hidden fields, which nobody can read through MCP |
| `reason` | string or null | `null` | Why the change helps, shown to the user with the proposal |

## Tools that end the session

### end_session

An end tool.

End the bwtidy session, like Log out in the web UI.

The answer comes first, the session ends right after it. The unsaved edits of
the web UI are written and the vault is locked. Until the user logs in again in
the bwtidy web UI and turns MCP on, every tool call fails with the answer that
MCP is disabled or that bwtidy is locked for this account.

No parameters.

### log_out_of_bitwarden

An end tool.

End the bwtidy session and log bwtidy's Bitwarden CLI out, like Log out with
Also log out of Bitwarden in the web UI. The next login in the web UI runs the
Bitwarden login again, and bitwarden.com may email a one-time code.

The answer comes first, the session ends right after it. The unsaved edits of
the web UI are written and the vault is locked. Until the user logs in again in
the bwtidy web UI and turns MCP on, every tool call fails with the answer that
MCP is disabled or that bwtidy is locked for this account.

No parameters.

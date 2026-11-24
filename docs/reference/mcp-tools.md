# MCP tools

<!-- Written by scripts/mcp-reference from the tools the MCP server registers. Do not edit it by
hand, run the script. -->

These are the tools bwtidy's MCP server offers an AI agent, as its client lists them. An agent
reads your vault without the secret values and proposes changes into the queue. It never writes:
a proposal waits on the Queue page until you approve or reject it. Why it works this way:
[What an agent can do](../explanation/mcp.md). How to connect a client:
[Connect an MCP client](../how-to/mcp.md).

Each tool is one of three kinds:

- read: reads the vault or the queue and changes nothing.
- propose: adds one entry to the queue and changes nothing in the vault until you approve it.
- end: ends the bwtidy session, like Log out in the web UI.

The descriptions are the text the agent reads, so they speak to the agent. While MCP is off,
every tool answers only that bwtidy MCP is disabled.

## Read tools

### list_items

Kind: read.

List the items of the vault, all or those matching every filter given. Each item has its id, name, type, folder, organization, collections, the other non-secret values by column (notes, login.uris, login.username, fields.&lt;name&gt;, ...), login.uris as a list of {uri, match} (match a word, null for the client's default, see propose_set_uris), attachments (id, fileName and size in bytes of each attachment, the size Bitwarden lists, which is the encrypted size, never the file's contents or url), secretColumns (the secret columns that hold a value) and template (the matching template and the fill score per template). The fill score counts the item type and every coordinate's datatype as one more thing to match each: total is the template's required coordinates (the ones not optional) plus 1 plus the coordinates with a datatype (an optional one only when it has a value), filled is the required coordinates with a value, plus 1 when typeMatches, plus a term from 0 to 1 per datatype counted in total, score is filled/total. A full match has filled == total, so a value off its format makes a near miss. A datatype's term is how well the value matches format, never whether it is right, and it is 0 without a value. A score with such terms has formats, one entry per non-secret column with a datatype: column, datatype (its name), score, distance (the least inserted, deleted or substituted characters that make the value match format, a score of 1 is a full match), firstEdit (the position in the value of the first of those edits), expected (what the format wants there), tooLong (too long to check, it scores 0). A secret column never shows a format: with a datatype it counts as set or not set only, and it has no entry in formats. Secret values are never available, only the names of the secret columns that are set.

| Parameter | Type | Default | Description |
| --- | --- | --- | --- |
| `text` | string or null | `null` | Case-insensitive text in the name, a file name or any non-secret value |
| `type` | string or null | `null` | login, note, card, identity or sshKey |
| `folder` | string or null | `null` | Folder name, empty for items without a folder |
| `organization` | string or null | `null` | Organization name, empty for items in the personal vault |
| `collection` | string or null | `null` | Collection name |
| `template` | string or null | `null` | Template name the item matches, empty for items matching none |

### get_item

Kind: read.

One item by id, shaped like an item of list_items. Secret values are never available, only the names of the secret columns that are set.

| Parameter | Type | Default | Description |
| --- | --- | --- | --- |
| `item_id` | string | required | The item id from list_items |

### list_folders

Kind: read.

The folders with the number of items in each (items) and of items in the trash still in it (itemsInTrash). Secret values are never available, only the names of the secret columns that are set.

No parameters.

### list_collections

Kind: read.

The organizations and their collections with the number of items in each. An organization item needs at least one collection. Secret values are never available, only the names of the secret columns that are set.

No parameters.

### list_templates

Kind: read.

The templates: name, description (when the user wrote one), item type and coordinates (a custom field coordinate carries its field type, 0 text, 1 hidden, 2 boolean, and a coordinate with a datatype names it, see list_datatypes, and an optional one has "optional": true). An item matches a template when it has the template's type, a value for every coordinate that is not optional and each value with a datatype matches format. An optional coordinate is not needed for a match and is never in missing, but a value it has must still match its datatype. A coordinate of kind fieldPattern stands for the custom fields whose name fits its pattern (\* matches any characters, case and surrounding spaces ignored) that no other coordinate names, counts once however many fit, and scores its datatype by the lowest of them. A secret column never shows a format: with a datatype it counts as set or not set only, and it has no entry in formats. Secret values are never available, only the names of the secret columns that are set.

No parameters.

### list_datatypes

Kind: read.

The datatypes: named formats the user defines once and template coordinates use. Each has name, pattern, description, ignoreCase, examples (values that match format), counterExamples (values that do not) and usedBy (the template coordinates that use it, each with template and column). The pattern must match the whole value, unless the datatype has wholeValue false: then it may match any part of the value (a ^ first or a $ last still anchors that side). A broken datatype counts as none and is not listed. A datatype that only secret columns use, or none, is not listed either. A datatype says how well a value matches format, never that a value is right. The fill scores of list_items and get_item show the results. A secret column never shows a format: with a datatype it counts as set or not set only, and it has no entry in formats. The user alone makes datatypes and corrects values, no tool proposes either. Secret values are never available, only the names of the secret columns that are set.

No parameters.

### list_near_duplicates

Kind: read.

Groups of custom field columns whose names are equal after case folding and trimming whitespace, candidates for propose_merge_columns or propose_rename_field. Secret values are never available, only the names of the secret columns that are set.

No parameters.

### list_proposals

Kind: read.

Your own proposals, newest first, at most 100: each with id (the entryId its propose call answered), summary (the one it answered), status, createdAt, decidedAt (when it left pending, null while pending) and reason. status is pending (waiting for the user), approved (being written), rejected (the user said no, do not propose it again), applied (written to the vault) or failed. reason is null except for a failed entry, then one word: conflict (an item changed since the proposal, list it again and propose again), merge conflict, write failed, interrupted, invalid (no longer fits the vault) or failed. Only proposals made through MCP are listed, never the web UI's edits. Secret values are never available, only the names of the secret columns that are set.

| Parameter | Type | Default | Description |
| --- | --- | --- | --- |
| `status` | `"pending"`, `"approved"`, `"rejected"`, `"applied"`, `"failed"` or null | `null` | Only the proposals with this status |

## Propose tools

### propose_move_to_folder

Kind: propose.

Propose moving items into a folder. A missing folder is created, an empty name takes the items out of their folder. This only proposes the change: it adds one entry to bwtidy's review queue and changes nothing in the vault. The user approves or rejects it in the bwtidy web UI. Answers the entry id and its summary, list_proposals shows what became of it. Secret values are never available. The user sees every proposal, and a proposal that would put a secret value into a non-secret column never reaches the vault.

| Parameter | Type | Default | Description |
| --- | --- | --- | --- |
| `item_ids` | list of string | required | Item ids from list_items |
| `folder` | string | required | Folder name, empty for no folder |
| `reason` | string or null | `null` | Why the change helps, shown to the user with the proposal |

### propose_rename_folder

Kind: propose.

Propose a new name for a folder. Its items stay in it. Refused when the name is empty or another folder has it, ignoring case. This only proposes the change: it adds one entry to bwtidy's review queue and changes nothing in the vault. The user approves or rejects it in the bwtidy web UI. Answers the entry id and its summary, list_proposals shows what became of it. Secret values are never available. The user sees every proposal, and a proposal that would put a secret value into a non-secret column never reaches the vault.

| Parameter | Type | Default | Description |
| --- | --- | --- | --- |
| `folder` | string | required | The folder's name as list_folders names it |
| `new_name` | string | required | The new name |
| `reason` | string or null | `null` | Why the change helps, shown to the user with the proposal |

### propose_delete_folder

Kind: propose.

Propose deleting an empty folder. A folder that holds an item, also one in the trash, is refused: move its items out first (propose_move_to_folder). list_folders counts the items of each folder. This only proposes the change: it adds one entry to bwtidy's review queue and changes nothing in the vault. The user approves or rejects it in the bwtidy web UI. Answers the entry id and its summary, list_proposals shows what became of it. Secret values are never available. The user sees every proposal, and a proposal that would put a secret value into a non-secret column never reaches the vault.

| Parameter | Type | Default | Description |
| --- | --- | --- | --- |
| `folder` | string | required | The folder's name as list_folders names it |
| `reason` | string or null | `null` | Why the change helps, shown to the user with the proposal |

### propose_set_collections

Kind: propose.

Propose the collections of an organization item, replacing the ones it has. Personal items go through propose_move_to_organization. This only proposes the change: it adds one entry to bwtidy's review queue and changes nothing in the vault. The user approves or rejects it in the bwtidy web UI. Answers the entry id and its summary, list_proposals shows what became of it. Secret values are never available. The user sees every proposal, and a proposal that would put a secret value into a non-secret column never reaches the vault.

| Parameter | Type | Default | Description |
| --- | --- | --- | --- |
| `item_id` | string | required | The item id from list_items |
| `collections` | list of string | required | Collection names, at least one |
| `reason` | string or null | `null` | Why the change helps, shown to the user with the proposal |

### propose_move_to_organization

Kind: propose.

Propose moving a personal item into an organization, in the given collections. Organization items go through propose_move_to_personal. This only proposes the change: it adds one entry to bwtidy's review queue and changes nothing in the vault. The user approves or rejects it in the bwtidy web UI. Answers the entry id and its summary, list_proposals shows what became of it. Secret values are never available. The user sees every proposal, and a proposal that would put a secret value into a non-secret column never reaches the vault.

| Parameter | Type | Default | Description |
| --- | --- | --- | --- |
| `item_id` | string | required | The item id from list_items |
| `organization` | string | required | Organization name |
| `collections` | list of string | required | Collection names of that organization, at least one |
| `reason` | string or null | `null` | Why the change helps, shown to the user with the proposal |

### propose_move_to_personal

Kind: propose.

Propose moving an organization item into the personal vault. The item is created anew in the personal vault with its attachments and the old one trashed, so its passkeys and password history are lost and everyone who shares its collections loses it. This only proposes the change: it adds one entry to bwtidy's review queue and changes nothing in the vault. The user approves or rejects it in the bwtidy web UI. Answers the entry id and its summary, list_proposals shows what became of it. Secret values are never available. The user sees every proposal, and a proposal that would put a secret value into a non-secret column never reaches the vault.

| Parameter | Type | Default | Description |
| --- | --- | --- | --- |
| `item_id` | string | required | The item id from list_items |
| `reason` | string or null | `null` | Why the change helps, shown to the user with the proposal |

### propose_rename_item

Kind: propose.

Propose a new name for an item. This only proposes the change: it adds one entry to bwtidy's review queue and changes nothing in the vault. The user approves or rejects it in the bwtidy web UI. Answers the entry id and its summary, list_proposals shows what became of it. Secret values are never available. The user sees every proposal, and a proposal that would put a secret value into a non-secret column never reaches the vault.

| Parameter | Type | Default | Description |
| --- | --- | --- | --- |
| `item_id` | string | required | The item id from list_items |
| `name` | string | required | The new name |
| `reason` | string or null | `null` | Why the change helps, shown to the user with the proposal |

### propose_set_uris

Kind: propose.

Propose the whole URI list of a login, each URI with its match setting, in the order given. It replaces the URIs the login has (get_item lists them), so give every URI to keep. A URI that is empty, or a regex that does not compile, is refused at once and nothing is queued. A URI's match says when autofill offers the login: domain (the base domain), host (the host and port), startsWith (the start of the URL), exact (the whole URL), regex (a regular expression, which Bitwarden evaluates as JavaScript, bwtidy refuses only one Python cannot compile either) or never. null is the client's default, a setting of the user's browser extension that is not in the vault. Only URIs and match settings change: no secret value is read or written. This only proposes the change: it adds one entry to bwtidy's review queue and changes nothing in the vault. The user approves or rejects it in the bwtidy web UI. Answers the entry id and its summary, list_proposals shows what became of it. Secret values are never available. The user sees every proposal, and a proposal that would put a secret value into a non-secret column never reaches the vault.

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

Kind: propose.

Propose one match setting for the URIs of logins: in each item, every URI that uri_pattern matches gets the match, the others keep theirs. uri_pattern is a shell glob over the whole URI, ignoring case (\*example.com\* for every URI holding example.com), every URI when left out. Items with no URI it changes are left out, and none left is an error. One queue entry holds every item. A URI's match says when autofill offers the login: domain (the base domain), host (the host and port), startsWith (the start of the URL), exact (the whole URL), regex (a regular expression, which Bitwarden evaluates as JavaScript, bwtidy refuses only one Python cannot compile either) or never. null is the client's default, a setting of the user's browser extension that is not in the vault. Only URIs and match settings change: no secret value is read or written. This only proposes the change: it adds one entry to bwtidy's review queue and changes nothing in the vault. The user approves or rejects it in the bwtidy web UI. Answers the entry id and its summary, list_proposals shows what became of it. Secret values are never available. The user sees every proposal, and a proposal that would put a secret value into a non-secret column never reaches the vault.

| Parameter | Type | Default | Description |
| --- | --- | --- | --- |
| `item_ids` | list of string | required | Item ids from list_items |
| `match` | `"domain"`, `"host"`, `"startsWith"`, `"exact"`, `"regex"`, `"never"` or null | required | domain, host, startsWith, exact, regex, never, or null for default |
| `uri_pattern` | string or null | `null` | A shell glob over the whole URI, ignoring case, default every URI |
| `reason` | string or null | `null` | Why the change helps, shown to the user with the proposal |

### propose_rename_field

Kind: propose.

Propose renaming a custom field in every item that has it, or in the items given. Type and position stay. An item that already has a field with the new name needs propose_merge_columns instead. This only proposes the change: it adds one entry to bwtidy's review queue and changes nothing in the vault. The user approves or rejects it in the bwtidy web UI. Answers the entry id and its summary, list_proposals shows what became of it. Secret values are never available. The user sees every proposal, and a proposal that would put a secret value into a non-secret column never reaches the vault.

| Parameter | Type | Default | Description |
| --- | --- | --- | --- |
| `from_name` | string | required | The field name now, verbatim |
| `to_name` | string | required | The new field name |
| `item_ids` | list of string or null | `null` | Only these items, default every item with the field |
| `reason` | string or null | `null` | Why the change helps, shown to the user with the proposal |

### propose_merge_columns

Kind: propose.

Propose merging columns into one target column, in every item that has one of the source columns or in the items given. Values not kept are removed. An item with values in more than one merged column needs a resolution naming the column to keep, the answer lists those items. A target built-in slot must exist in the item's type, a type change goes through propose_apply_template. This only proposes the change: it adds one entry to bwtidy's review queue and changes nothing in the vault. The user approves or rejects it in the bwtidy web UI. Answers the entry id and its summary, list_proposals shows what became of it. Secret values are never available. The user sees every proposal, and a proposal that would put a secret value into a non-secret column never reaches the vault.

| Parameter | Type | Default | Description |
| --- | --- | --- | --- |
| `sources` | list of string | required | Source columns, like fields.User or login.username |
| `target` | string | required | The column that keeps the value |
| `item_ids` | list of string or null | `null` | Only these items, default every item with a source column |
| `resolutions` | object of string or null | `null` | {item id: the merged column whose value to keep} |
| `reason` | string or null | `null` | Why the change helps, shown to the user with the proposal |

### propose_apply_template

Kind: propose.

Propose applying a template to an item, with a mapping {template column: the item's column}. Mapped values move, unmapped leaves stay, missing template fields are created empty. A template of another type converts the item (a new item is created and the old one goes to the trash). This only proposes the change: it adds one entry to bwtidy's review queue and changes nothing in the vault. The user approves or rejects it in the bwtidy web UI. Answers the entry id and its summary, list_proposals shows what became of it. Secret values are never available. The user sees every proposal, and a proposal that would put a secret value into a non-secret column never reaches the vault.

| Parameter | Type | Default | Description |
| --- | --- | --- | --- |
| `item_id` | string | required | The item id from list_items |
| `template` | string | required | Template name from list_templates |
| `mapping` | object of string or null | `null` | {template column: the item's column}, default none |
| `reason` | string or null | `null` | Why the change helps, shown to the user with the proposal |

### propose_convert_item

Kind: propose.

Propose converting an item to another type: a new item is created with the new type and the old one goes to the trash, both after the user approves. The mapping is {target column: the item's column}. Mapped columns move (a mapped custom field is dropped from the old place, mapped notes are cleared), unmapped built-in values of the old type become custom fields, attachments are copied. No value passes through MCP: the values move inside the vault. A secret column may not be mapped into a non-secret one. An SSH key needs its private key mapped from a column of the item, for example {"sshKey.privateKey": "notes"} for a secure note that holds the key, and its public key and fingerprint are filled in from it when they are empty. This only proposes the change: it adds one entry to bwtidy's review queue and changes nothing in the vault. The user approves or rejects it in the bwtidy web UI. Answers the entry id and its summary, list_proposals shows what became of it. Secret values are never available. The user sees every proposal, and a proposal that would put a secret value into a non-secret column never reaches the vault.

| Parameter | Type | Default | Description |
| --- | --- | --- | --- |
| `item_id` | string | required | The item id from list_items |
| `type` | string | required | The new type: login, secureNote, card, identity or sshKey |
| `mapping` | object of string or null | `null` | {target column: the item's column}, default none |
| `reason` | string or null | `null` | Why the change helps, shown to the user with the proposal |

### propose_merge_items

Kind: propose.

Propose merging one item into another: the mapped columns of the source item move into the target item ({target column: source column}), then the source item goes to the trash. A new field gets the type of its source, so a hidden value stays hidden. This only proposes the change: it adds one entry to bwtidy's review queue and changes nothing in the vault. The user approves or rejects it in the bwtidy web UI. Answers the entry id and its summary, list_proposals shows what became of it. Secret values are never available. The user sees every proposal, and a proposal that would put a secret value into a non-secret column never reaches the vault.

| Parameter | Type | Default | Description |
| --- | --- | --- | --- |
| `from_item_id` | string | required | The item that goes to the trash |
| `to_item_id` | string | required | The item that receives the values |
| `mapping` | object of string | required | {target column: source column}, columns as list_items names them |
| `reason` | string or null | `null` | Why the change helps, shown to the user with the proposal |

### propose_rename_attachment

Kind: propose.

Propose a new file name for an attachment of an item, its id as list_items and get_item show it. When the user approves, bwtidy renames it itself: it uploads the file again under the new name and deletes the old one, so the attachment gets a new id. The contents never pass through MCP, no tool reads or answers them. Refused when the name is empty, holds a slash or a backslash, is the name it has now, or is the name of another attachment of the item (ignoring case). This only proposes the change: it adds one entry to bwtidy's review queue and changes nothing in the vault. The user approves or rejects it in the bwtidy web UI. Answers the entry id and its summary, list_proposals shows what became of it. Secret values are never available. The user sees every proposal, and a proposal that would put a secret value into a non-secret column never reaches the vault.

| Parameter | Type | Default | Description |
| --- | --- | --- | --- |
| `item_id` | string | required | The item id from list_items |
| `attachment_id` | string | required | The attachment's id from list_items or get_item |
| `new_name` | string | required | The new file name |
| `reason` | string or null | `null` | Why the change helps, shown to the user with the proposal |

### propose_trash_item

Kind: propose.

Propose moving an item to the trash (never a permanent delete). This only proposes the change: it adds one entry to bwtidy's review queue and changes nothing in the vault. The user approves or rejects it in the bwtidy web UI. Answers the entry id and its summary, list_proposals shows what became of it. Secret values are never available. The user sees every proposal, and a proposal that would put a secret value into a non-secret column never reaches the vault.

| Parameter | Type | Default | Description |
| --- | --- | --- | --- |
| `item_id` | string | required | The item id from list_items |
| `reason` | string or null | `null` | Why the change helps, shown to the user with the proposal |

### propose_create_item

Kind: propose.

Propose a new item, from a raw type or from a template (give exactly one). A template brings its type and its custom fields, created empty with their field types. values sets non-secret columns by name, like list_items names them (login.username, login.uris, notes, fields.&lt;name&gt;, ...), a custom field the template does not name becomes a text field. Secret values are never taken, a value for a secret column is refused. A secret field (password, TOTP, card number or code, private key, hidden field) is either left blank or generated. Fields left blank start empty, so the item shows as near its template, and the user fills them in the bwtidy web UI, where they show up in the To fill in list. A login.password or a hidden field can instead get a value generated by bw when the item is written (generate_secrets, or generate_password for a login's password), which nobody sees until the user reveals it in the web UI and nobody can read through MCP. It follows the default generator settings the user keeps on the Settings page. An SSH key is refused, its private key cannot come through MCP: propose_convert_item from an item that holds the key is the way. This only proposes the change: it adds one entry to bwtidy's review queue and changes nothing in the vault. The user approves or rejects it in the bwtidy web UI. Answers the entry id and its summary, list_proposals shows what became of it. Secret values are never available. The user sees every proposal, and a proposal that would put a secret value into a non-secret column never reaches the vault.

| Parameter | Type | Default | Description |
| --- | --- | --- | --- |
| `name` | string | required | The new item's name |
| `type` | string or null | `null` | login, secureNote, card or identity, when no template |
| `template` | string or null | `null` | Template name from list_templates, when no type |
| `folder` | string | `""` | Folder name, created when missing, empty for none |
| `organization` | string | `""` | Organization name, empty for the personal vault |
| `collections` | list of string or null | `null` | Collection names of the organization, at least one with one |
| `values` | object of (string, integer, boolean or list of string) or null | `null` | {column: value} for non-secret columns, default none |
| `generate_password` | boolean | `false` | A login only: bw generates its password at write time, following the default generator settings |
| `generate_secrets` | list of string or null | `null` | Secret columns bw generates at write time, login.password or hidden fields, which nobody can read through MCP |
| `reason` | string or null | `null` | Why the change helps, shown to the user with the proposal |

## Tools that end the session

### end_session

Kind: end.

End the bwtidy session, like Log out in the web UI. The answer comes first, the session ends right after it: the unsaved edits of the web UI are written, the vault is locked and every tool answers that MCP is disabled until the user logs in again in the bwtidy web UI and turns MCP on.

No parameters.

### log_out_of_bitwarden

Kind: end.

End the bwtidy session and log bwtidy's Bitwarden CLI out, like Log out with Also log out of Bitwarden in the web UI. The next login in the web UI runs the Bitwarden login again, and bitwarden.com may email a one-time code. The answer comes first, the session ends right after it: the unsaved edits of the web UI are written, the vault is locked and every tool answers that MCP is disabled until the user logs in again in the bwtidy web UI and turns MCP on.

No parameters.

"""What the agent reads: the instructions, the notes the tools share and each tool's description.

The descriptions follow the order the server lists the tools in (`TOOLS` in server.py). Each is
short paragraphs and lists, a little Markdown (`code` for a column or a key, `- ` for a list
entry) that reads as it is and that scripts/mcp-reference renders on docs/reference/mcp-tools.md.
"""

from typing import Annotated

from pydantic import Field

DISABLED = "bwtidy MCP is disabled. A logged in user must enable it in the web UI."

INSTRUCTIONS = """\
bwtidy shows the user's Bitwarden vault without its secret values and takes proposals for
reorganizing it. Read with the list and get tools. Every propose tool only adds an entry to
bwtidy's review queue and changes nothing in the vault: the user approves or rejects each entry in
the bwtidy web UI. list_proposals shows what became of your proposals (pending, approved,
rejected, applied or failed): read it before proposing, and do not propose again what the user
rejected. end_session and log_out_of_bitwarden end the bwtidy session (MCP is off until the user
logs in again), call them only when asked to. Secret values (passwords, TOTP seeds, card
numbers and codes, private keys, hidden custom fields) are never available, only which secret
columns an item has set.
Columns are named like the grid: name, notes, folder, login.username, login.uris,
fields.<custom field name>, and so on. login.uris lists each URI with its match setting. A
datatype is a named format the user defines for template coordinates (list_datatypes). Fill scores
say how well a non-secret value matches its format, a secret column never shows a format, only
whether it is set.
"""


def paragraphs(*blocks: str) -> str:
    """Blocks of a description, a blank line between them."""
    return "\n\n".join(blocks)


def bullets(*entries: str) -> str:
    """A list of a description, one line per entry."""
    return "\n".join(f"- {entry}" for entry in entries)


READ_NOTE = "Secret values are never available, only the names of the secret columns that are set."
SECRET_FORMAT_NOTE = (
    "A secret column never shows a format: with a datatype it counts as set or not set only, and"
    " it has no entry in `formats`."
)
PROPOSE_NOTE = (
    "This only proposes the change: it adds one entry to bwtidy's review queue and changes"
    " nothing in the vault. The user approves or rejects it in the bwtidy web UI. Answers the"
    " entry id and its summary, list_proposals shows what became of it. Secret values are"
    " never available. The user sees every proposal, and a proposal that would put a secret value"
    " into a non-secret column never reaches the vault."
)
END_NOTE = (
    "The answer comes first, the session ends right after it: the unsaved edits of the web UI"
    " are written, the vault is locked and every tool answers that MCP is disabled until the user"
    " logs in again in the bwtidy web UI and turns MCP on."
)

## the columns an item shows outside "values", because every filter and proposal names them
TOP_COLUMNS = ("name", "type", "folder", "organization", "collections")

ItemId = Annotated[str, Field(description="The item id from list_items")]

MATCH_NOTE = paragraphs(
    "A URI's `match` says when autofill offers the login:",
    bullets(
        "`domain`: the base domain.",
        "`host`: the host and port.",
        "`startsWith`: the start of the URL.",
        "`exact`: the whole URL.",
        "`regex`: a regular expression, which Bitwarden evaluates as JavaScript. bwtidy refuses"
        " only one Python cannot compile either.",
        "`never`.",
        "null is the client's default, a setting of the user's browser extension that is not in"
        " the vault.",
    ),
    "Only URIs and match settings change: no secret value is read or written.",
)

LIST_ITEMS = paragraphs(
    "List the items of the vault: all of them, or those matching every filter given.",
    "Each item has:",
    bullets(
        f"`id`, {', '.join(f'`{column}`' for column in TOP_COLUMNS[:-1])} and `{TOP_COLUMNS[-1]}`.",
        "The other non-secret values by column (`notes`, `login.uris`, `login.username`,"
        " `fields.<name>`, ...).",
        "`login.uris` as a list of `{uri, match}`: match a word, null for the client's default"
        " (see propose_set_uris).",
        "`attachments`: the id, fileName and size in bytes of each attachment. The size is the one"
        " Bitwarden lists, which is the encrypted size. Never the file's contents or url.",
        "`secretColumns`: the secret columns that hold a value.",
        "`template`: the matching template and the fill score per template.",
    ),
    "The fill score counts the item type and every coordinate's datatype as one more thing to"
    " match each:",
    bullets(
        "`total`: the template's required coordinates (the ones not optional), plus 1, plus the"
        " coordinates with a datatype (an optional one only when it has a value).",
        "`filled`: the required coordinates with a value, plus 1 when `typeMatches`, plus a term"
        " from 0 to 1 per datatype counted in total.",
        "`score`: filled/total. A full match has filled == total, so a value off its format makes"
        " a near miss.",
    ),
    "A datatype's term is how well the value matches format, never whether it is right, and it"
    " is 0 without a value. A score with such terms has `formats`, one entry per non-secret"
    " column with a datatype:",
    bullets(
        "`column`.",
        "`datatype`: its name.",
        "`score`: 1 is a full match.",
        "`distance`: the least inserted, deleted or substituted characters that make the value"
        " match format.",
        "`firstEdit`: the position in the value of the first of those edits.",
        "`expected`: what the format wants there.",
        "`tooLong`: too long to check, it scores 0.",
    ),
    SECRET_FORMAT_NOTE,
    READ_NOTE,
)

GET_ITEM = paragraphs("One item by id, shaped like an item of list_items.", READ_NOTE)

LIST_FOLDERS = paragraphs(
    "The folders with the number of items in each (`items`) and of items in the trash still"
    " in it (`itemsInTrash`).",
    READ_NOTE,
)

LIST_COLLECTIONS = paragraphs(
    "The organizations and their collections with the number of items in each. An"
    " organization item needs at least one collection.",
    READ_NOTE,
)

LIST_TEMPLATES = paragraphs(
    "The templates. Each has:",
    bullets(
        "`name`.",
        "`description`, when the user wrote one.",
        "The item type.",
        "`coordinates`. A custom field coordinate carries its field type (0 text, 1 hidden,"
        " 2 boolean). A coordinate with a datatype names it (see list_datatypes). An optional"
        ' one has `"optional": true`.',
    ),
    "An item matches a template when it has the template's type, a value for every coordinate"
    " that is not optional and each value with a datatype matches format.",
    "An optional coordinate is not needed for a match and is never in `missing`, but a value it"
    " has must still match its datatype.",
    "A coordinate of kind fieldPattern stands for the custom fields whose name fits its pattern"
    " that no other coordinate names. In the pattern `*` matches any characters, case and"
    " surrounding spaces are ignored. It counts once however many fit, and scores its datatype"
    " by the lowest of them.",
    SECRET_FORMAT_NOTE,
    READ_NOTE,
)

LIST_DATATYPES = paragraphs(
    "The datatypes: named formats the user defines once and template coordinates use. Each has:",
    bullets(
        "`name`, `pattern`, `description` and `ignoreCase`.",
        "`examples`: values that match format.",
        "`counterExamples`: values that do not.",
        "`usedBy`: the template coordinates that use it, each with `template` and `column`.",
    ),
    "The pattern must match the whole value, unless the datatype has `wholeValue` false: then it"
    " may match any part of the value (a `^` first or a `$` last still anchors that side).",
    "Not listed: a broken datatype, which counts as none, and a datatype that only secret"
    " columns use, or none.",
    "A datatype says how well a value matches format, never that a value is right. The fill"
    " scores of list_items and get_item show the results.",
    SECRET_FORMAT_NOTE,
    "The user alone makes datatypes and corrects values, no tool proposes either.",
    READ_NOTE,
)

LIST_NEAR_DUPLICATES = paragraphs(
    "Groups of custom field columns whose names are equal after case folding and trimming"
    " whitespace, candidates for propose_merge_columns or propose_rename_field.",
    READ_NOTE,
)

LIST_PROPOSALS = paragraphs(
    "Your own proposals, newest first, at most 100. Only proposals made through MCP are listed,"
    " never the web UI's edits. Each has:",
    bullets(
        "`id`: the entryId its propose call answered.",
        "`summary`: the one it answered.",
        "`status`.",
        "`createdAt`.",
        "`decidedAt`: when it left pending, null while pending.",
        "`reason`.",
    ),
    "`status` is one of:",
    bullets(
        "`pending`: waiting for the user.",
        "`approved`: being written.",
        "`rejected`: the user said no, do not propose it again.",
        "`applied`: written to the vault.",
        "`failed`.",
    ),
    "`reason` is null except for a failed entry, then one word:",
    bullets(
        "`conflict`: an item changed since the proposal, list it again and propose again.",
        "`merge conflict`.",
        "`write failed`.",
        "`interrupted`.",
        "`invalid`: no longer fits the vault.",
        "`failed`.",
    ),
    READ_NOTE,
)

PROPOSE_MOVE_TO_FOLDER = paragraphs(
    "Propose moving items into a folder. A missing folder is created, an empty name takes"
    " the items out of their folder.",
    PROPOSE_NOTE,
)

PROPOSE_RENAME_FOLDER = paragraphs(
    "Propose a new name for a folder. Its items stay in it. Refused when the name is empty or"
    " another folder has it, ignoring case.",
    PROPOSE_NOTE,
)

PROPOSE_DELETE_FOLDER = paragraphs(
    "Propose deleting an empty folder. A folder that holds an item, also one in the trash,"
    " is refused: move its items out first (propose_move_to_folder). list_folders counts"
    " the items of each folder.",
    PROPOSE_NOTE,
)

PROPOSE_SET_COLLECTIONS = paragraphs(
    "Propose the collections of an organization item, replacing the ones it has. Personal"
    " items go through propose_move_to_organization.",
    PROPOSE_NOTE,
)

PROPOSE_MOVE_TO_ORGANIZATION = paragraphs(
    "Propose moving a personal item into an organization, in the given collections."
    " Organization items go through propose_move_to_personal.",
    PROPOSE_NOTE,
)

PROPOSE_MOVE_TO_PERSONAL = paragraphs(
    "Propose moving an organization item into the personal vault.",
    "The item is created anew in the personal vault with its attachments and the old one"
    " trashed, so its passkeys and password history are lost and everyone who shares its"
    " collections loses it.",
    PROPOSE_NOTE,
)

PROPOSE_RENAME_ITEM = paragraphs("Propose a new name for an item.", PROPOSE_NOTE)

PROPOSE_SET_URIS = paragraphs(
    "Propose the whole URI list of a login, each URI with its match setting, in the order given.",
    "It replaces the URIs the login has (get_item lists them), so give every URI to keep. A URI"
    " that is empty, or a regex that does not compile, is refused at once and nothing is"
    " queued.",
    MATCH_NOTE,
    PROPOSE_NOTE,
)

PROPOSE_SET_URI_MATCH = paragraphs(
    "Propose one match setting for the URIs of logins: in each item, every URI that"
    " `uri_pattern` matches gets the match, the others keep theirs.",
    bullets(
        "`uri_pattern` is a shell glob over the whole URI, ignoring case (`*example.com*` for"
        " every URI holding example.com), every URI when left out.",
        "Items with no URI it changes are left out, and none left is an error.",
        "One queue entry holds every item.",
    ),
    MATCH_NOTE,
    PROPOSE_NOTE,
)

PROPOSE_RENAME_FIELD = paragraphs(
    "Propose renaming a custom field in every item that has it, or in the items given. Type"
    " and position stay.",
    "An item that already has a field with the new name needs propose_merge_columns instead.",
    PROPOSE_NOTE,
)

PROPOSE_MERGE_COLUMNS = paragraphs(
    "Propose merging columns into one target column, in every item that has one of the"
    " source columns or in the items given.",
    bullets(
        "Values not kept are removed.",
        "An item with values in more than one merged column needs a resolution naming the"
        " column to keep, the answer lists those items.",
        "A target built-in slot must exist in the item's type, a type change goes through"
        " propose_apply_template.",
    ),
    PROPOSE_NOTE,
)

PROPOSE_APPLY_TEMPLATE = paragraphs(
    "Propose applying a template to an item, with a mapping `{template column: the item's"
    " column}`.",
    bullets(
        "Mapped values move.",
        "Unmapped leaves stay.",
        "Missing template fields are created empty.",
        "A template of another type converts the item (a new item is created and the old one"
        " goes to the trash).",
    ),
    PROPOSE_NOTE,
)

PROPOSE_CONVERT_ITEM = paragraphs(
    "Propose converting an item to another type: a new item is created with the new type"
    " and the old one goes to the trash, both after the user approves.",
    "The mapping is `{target column: the item's column}`:",
    bullets(
        "Mapped columns move: a mapped custom field is dropped from the old place, mapped notes"
        " are cleared.",
        "Unmapped built-in values of the old type become custom fields.",
        "Attachments are copied.",
    ),
    "No value passes through MCP: the values move inside the vault. A secret column may not be"
    " mapped into a non-secret one.",
    "An SSH key needs its private key mapped from a column of the item, for example"
    ' `{"sshKey.privateKey": "notes"}` for a secure note that holds the key. Its public key and'
    " fingerprint are filled in from it when they are empty.",
    PROPOSE_NOTE,
)

PROPOSE_MERGE_ITEMS = paragraphs(
    "Propose merging one item into another: the mapped columns of the source item move into"
    " the target item (`{target column: source column}`), then the source item goes to the"
    " trash.",
    "A new field gets the type of its source, so a hidden value stays hidden.",
    PROPOSE_NOTE,
)

PROPOSE_RENAME_ATTACHMENT = paragraphs(
    "Propose a new file name for an attachment of an item, its id as list_items and get_item"
    " show it.",
    "When the user approves, bwtidy renames it itself: it uploads the file again under the new"
    " name and deletes the old one, so the attachment gets a new id. The contents never pass"
    " through MCP, no tool reads or answers them.",
    "Refused when the name is empty, holds a slash or a backslash, is the name it has now, or is"
    " the name of another attachment of the item (ignoring case).",
    PROPOSE_NOTE,
)

PROPOSE_TRASH_ITEM = paragraphs(
    "Propose moving an item to the trash (never a permanent delete).", PROPOSE_NOTE
)

PROPOSE_CREATE_ITEM = paragraphs(
    "Propose a new item, from a raw type or from a template (give exactly one).",
    bullets(
        "A template brings its type and its custom fields, created empty with their field types.",
        "`values` sets non-secret columns by name, like list_items names them"
        " (`login.username`, `login.uris`, `notes`, `fields.<name>`, ...). A custom field the"
        " template does not name becomes a text field.",
    ),
    "Secret values are never taken, a value for a secret column is refused. A secret field"
    " (password, TOTP, card number or code, private key, hidden field) is either left blank or"
    " generated:",
    bullets(
        "Left blank: fields left blank start empty, so the item shows as near its template, and"
        " the user fills them in the bwtidy web UI, where they show up in the To fill in list.",
        "Generated: a `login.password` or a hidden field can instead get a value generated by bw"
        " when the item is written (`generate_secrets`, or `generate_password` for a login's"
        " password), which nobody sees until the user reveals it in the web UI and nobody can"
        " read through MCP. It follows the default generator settings the user keeps on the"
        " Settings page.",
    ),
    "An SSH key is refused, its private key cannot come through MCP: propose_convert_item from"
    " an item that holds the key is the way.",
    PROPOSE_NOTE,
)

END_SESSION = paragraphs("End the bwtidy session, like Log out in the web UI.", END_NOTE)

LOG_OUT_OF_BITWARDEN = paragraphs(
    "End the bwtidy session and log bwtidy's Bitwarden CLI out, like Log out with Also log"
    " out of Bitwarden in the web UI. The next login in the web UI runs the Bitwarden login"
    " again, and bitwarden.com may email a one-time code.",
    END_NOTE,
)

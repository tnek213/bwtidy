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
    "A secret column never shows a format. With a datatype it counts only as set or not set, and"
    " it has no entry in `formats`."
)
PROPOSE_NOTE = (
    "This only proposes the change. It adds one entry to bwtidy's review queue and changes"
    " nothing in the vault. The user approves or rejects it in the bwtidy web UI. The answer is"
    " the entry's `entryId` and `summary`, and list_proposals shows what became of the entry."
    " Secret values are never available. The user sees every proposal, and a proposal that would"
    " put a secret value into a non-secret column never reaches the vault."
)
END_NOTE = (
    "The answer comes first, the session ends right after it. The unsaved edits of the web UI are"
    " written and the vault is locked. Until the user logs in again in the bwtidy web UI and turns"
    " MCP on, every tool call fails with the answer that MCP is disabled or that bwtidy is locked"
    " for this account."
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
        "`regex`: a regular expression, which Bitwarden evaluates as JavaScript. bwtidy checks"
        " it with Python and refuses only one that Python cannot compile.",
        "`never`.",
        "null is the client's default, a setting of the user's browser extension that is not in"
        " the vault.",
    ),
    "Only URIs and match settings change. No secret value is read or written.",
)

LIST_ITEMS = paragraphs(
    "List the items of the vault, all of them or only those that match every filter given.",
    "Each item has:",
    bullets(
        f"`id`, {', '.join(f'`{column}`' for column in TOP_COLUMNS[:-1])} and `{TOP_COLUMNS[-1]}`.",
        "`values`: the other non-secret values by column (`notes`, `login.uris`,"
        " `login.username`, `fields.<name>`, ...). Its `login.uris` is a list of `{uri, match}`,"
        " where `match` is a word or null for the client's default (see propose_set_uris).",
        "`attachments`: the id, fileName and size in bytes of each attachment. The size is the"
        " encrypted size that Bitwarden lists. The file's contents and url are never shown.",
        "`secretColumns`: the secret columns that hold a value.",
        "`template`: `match`, the name of the template the item matches or null, and `scores`,"
        " one fill score per template.",
    ),
    "A fill score counts the item type as one more thing to match, and each coordinate's"
    " datatype as one more too:",
    bullets(
        "`total`: the template's required coordinates (the ones not optional), plus 1 for the"
        " type, plus the coordinates with a datatype. An optional coordinate with a datatype"
        " counts only when it has a value.",
        "`filled`: the required coordinates with a value, plus 1 when `typeMatches`, plus a term"
        " from 0 to 1 for each datatype counted in `total`.",
        "`score`: filled/total. A full match has filled == total, so a value off its format makes"
        " a near miss.",
        "`missing`: the required coordinates without a value.",
    ),
    "A datatype's term says how well the value matches format, never whether the value is"
    " right. It is 0 without a value. A score with such terms has `formats`, one entry per"
    " non-secret column with a datatype:",
    bullets(
        "`column`.",
        "`datatype`: its name.",
        "`score`: 1 is a full match.",
        "`distance`: the fewest characters to insert, delete or substitute so that the value"
        " matches format.",
        "`firstEdit`: the position in the value of the first of those edits.",
        "`expected`: what the format wants there.",
        "`tooLong`: the value is too long to check, so it scores 0.",
    ),
    SECRET_FORMAT_NOTE,
    READ_NOTE,
)

GET_ITEM = paragraphs(
    "Get one item by its id. It has the same shape as an item of list_items.", READ_NOTE
)

LIST_FOLDERS = paragraphs(
    "List the folders. Each has its `name`, the number of items in it (`items`) and the number"
    " of items in the trash that are still in it (`itemsInTrash`). `itemsWithoutFolder` counts"
    " the items outside the trash that are in no folder.",
    READ_NOTE,
)

LIST_COLLECTIONS = paragraphs(
    "List the organizations and their collections, with the number of items in each collection."
    " An organization item needs at least one collection.",
    READ_NOTE,
)

LIST_TEMPLATES = paragraphs(
    "List the templates. Each has:",
    bullets(
        "`name`.",
        "`description`, when the user wrote one.",
        "The item type.",
        "`coordinates`. A custom field coordinate carries its field type (0 text, 1 hidden,"
        " 2 boolean). A coordinate with a datatype names it (see list_datatypes). An optional"
        ' one has `"optional": true`.',
    ),
    "An item matches a template when it has the template's type and a value for every"
    " coordinate that is not optional, and when each value with a datatype matches format.",
    "An optional coordinate is not needed for a match and is never in `missing`. A value it has"
    " must still match its datatype.",
    "A coordinate of kind fieldPattern stands for every custom field whose name fits its pattern"
    " and that no other coordinate names. In the pattern `*` matches any characters. Case and"
    " surrounding spaces are ignored. The coordinate counts once however many fields fit, and its"
    " datatype term is the lowest of theirs.",
    SECRET_FORMAT_NOTE,
    READ_NOTE,
)

LIST_DATATYPES = paragraphs(
    "List the datatypes, the named formats the user defines once and template coordinates use."
    " Each has:",
    bullets(
        "`name`, `pattern`, `description` and `ignoreCase`.",
        "`examples`: values that match format.",
        "`counterExamples`: values that do not match format.",
        "`usedBy`: the template coordinates that use it, each with `template` and `column`.",
    ),
    "The pattern must match the whole value. A datatype with `wholeValue` false may match any"
    " part of the value instead, and there a `^` first or a `$` last still anchors that side.",
    "A broken datatype counts as no datatype and is not listed. Nor is a datatype that no"
    " non-secret template coordinate uses.",
    "A datatype says how well a value matches format, never that a value is right. The fill"
    " scores of list_items and get_item show the results.",
    SECRET_FORMAT_NOTE,
    "Only the user makes datatypes and corrects values, and no tool proposes either.",
    READ_NOTE,
)

LIST_NEAR_DUPLICATES = paragraphs(
    "List groups of custom field columns whose names are equal after case folding and trimming"
    " whitespace. Each group is a candidate for propose_merge_columns or propose_rename_field.",
    READ_NOTE,
)

LIST_PROPOSALS = paragraphs(
    "List the proposals made through MCP, newest first and at most 100. The web UI's edits are"
    " never listed. Each proposal has:",
    bullets(
        "`id`: the `entryId` its propose call answered.",
        "`summary`: the `summary` its propose call answered.",
        "`status`.",
        "`createdAt`.",
        "`decidedAt`: when it left pending, null while pending.",
        "`reason`.",
    ),
    "`status` is one of:",
    bullets(
        "`pending`: waiting for the user.",
        "`approved`: being written.",
        "`rejected`: the user said no. Do not propose it again.",
        "`applied`: written to the vault.",
        "`failed`.",
    ),
    "`reason` is null except for a failed entry, which has one of these fixed reasons:",
    bullets(
        "`conflict`: an item changed since the proposal. List it again and propose again.",
        "`merge conflict`.",
        "`write failed`.",
        "`interrupted`.",
        "`invalid`: no longer fits the vault.",
        "`failed`.",
    ),
    READ_NOTE,
)

PROPOSE_MOVE_TO_FOLDER = paragraphs(
    "Propose moving items into a folder. A folder that does not exist yet is created with the"
    " move. An empty name takes the items out of their folder.",
    PROPOSE_NOTE,
)

PROPOSE_RENAME_FOLDER = paragraphs(
    "Propose a new name for a folder. Its items stay in it. It is refused when the name is empty"
    " or when another folder has it, ignoring case.",
    PROPOSE_NOTE,
)

PROPOSE_DELETE_FOLDER = paragraphs(
    "Propose deleting an empty folder. A folder that holds an item is refused, and so is one"
    " whose only items are in the trash. Move its items out first (propose_move_to_folder)."
    " list_folders counts the items of each folder, in the trash too.",
    PROPOSE_NOTE,
)

PROPOSE_SET_COLLECTIONS = paragraphs(
    "Propose the collections of an organization item, replacing the ones it has. A personal item"
    " goes through propose_move_to_organization instead.",
    PROPOSE_NOTE,
)

PROPOSE_MOVE_TO_ORGANIZATION = paragraphs(
    "Propose moving a personal item into an organization, in the given collections. An"
    " organization item goes through propose_move_to_personal instead.",
    PROPOSE_NOTE,
)

PROPOSE_MOVE_TO_PERSONAL = paragraphs(
    "Propose moving an organization item into the personal vault.",
    "The item is created anew in the personal vault with its attachments, and the old one goes"
    " to the trash. Its passkeys and password history are lost, and everyone who shares its"
    " collections loses the item.",
    PROPOSE_NOTE,
)

PROPOSE_RENAME_ITEM = paragraphs("Propose a new name for an item.", PROPOSE_NOTE)

PROPOSE_SET_URIS = paragraphs(
    "Propose the whole URI list of a login, each URI with its match setting, in the order given.",
    "The list replaces the URIs the login has (get_item lists them), so give every URI to keep."
    " An empty URI or a regex that does not compile is refused at once, and nothing is queued.",
    MATCH_NOTE,
    PROPOSE_NOTE,
)

PROPOSE_SET_URI_MATCH = paragraphs(
    "Propose one match setting for the URIs of logins. In each item every URI that"
    " `uri_pattern` matches gets the match, and the other URIs keep theirs.",
    bullets(
        "`uri_pattern` is a shell glob over the whole URI, ignoring case (`*example.com*` for"
        " every URI holding example.com). Without it every URI matches.",
        "An item whose URIs would all keep their match is left out. When no item is left, the"
        " call is an error and nothing is queued.",
        "One queue entry holds every item.",
    ),
    MATCH_NOTE,
    PROPOSE_NOTE,
)

PROPOSE_RENAME_FIELD = paragraphs(
    "Propose renaming a custom field in every item that has it, or only in the items given. The"
    " field keeps its type and position.",
    "An item that already has a field with the new name needs propose_merge_columns instead.",
    PROPOSE_NOTE,
)

PROPOSE_MERGE_COLUMNS = paragraphs(
    "Propose merging columns into one target column, in every item that has one of the source"
    " columns or only in the items given.",
    bullets(
        "Values not kept are removed.",
        "An item with values in more than one merged column needs a resolution that names the"
        " column to keep. Without one the call is an error that lists those items.",
        "A built-in target slot must exist in the item's type. A type change goes through"
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
    "Propose converting an item to another type. A new item is created with the new type and"
    " the old one goes to the trash, both after the user approves.",
    "The mapping is `{target column: the item's column}`:",
    bullets(
        "Mapped columns move. A mapped custom field is dropped from its old place, and mapped"
        " notes are cleared.",
        "Unmapped built-in values of the old type become custom fields.",
        "Attachments are copied.",
    ),
    "No value passes through MCP, since the values move inside the vault. A secret column may"
    " not be mapped into a non-secret one.",
    "An SSH key needs its private key mapped from a column of the item, for example"
    ' `{"sshKey.privateKey": "notes"}` for a secure note that holds the key. Its public key and'
    " fingerprint are filled in from it when they are empty.",
    PROPOSE_NOTE,
)

PROPOSE_MERGE_ITEMS = paragraphs(
    "Propose merging one item into another. The mapped columns of the source item move into the"
    " target item (`{target column: source column}`), then the source item goes to the trash.",
    "A new field gets the type of its source, so a hidden value stays hidden.",
    PROPOSE_NOTE,
)

PROPOSE_RENAME_ATTACHMENT = paragraphs(
    "Propose a new file name for an attachment of an item. Name the attachment by the id that"
    " list_items and get_item show.",
    "When the user approves, bwtidy renames it itself. It uploads the file again under the new"
    " name and deletes the old one, so the attachment gets a new id. The contents never pass"
    " through MCP, and no tool reads or answers them.",
    "The name is refused when it is empty, holds a slash or a backslash, is the name the"
    " attachment has now or is the name of another attachment of the item (ignoring case).",
    PROPOSE_NOTE,
)

PROPOSE_TRASH_ITEM = paragraphs(
    "Propose moving an item to the trash (never a permanent delete).", PROPOSE_NOTE
)

PROPOSE_CREATE_ITEM = paragraphs(
    "Propose a new item from a raw type or from a template. Give exactly one of the two.",
    bullets(
        "A template brings its type and its custom fields, created empty with their field types.",
        "`values` sets non-secret columns by name, as list_items names them (`login.username`,"
        " `login.uris`, `notes`, `fields.<name>`, ...). A custom field that the template does"
        " not name becomes a text field.",
    ),
    "Secret values are never taken, and a value for a secret column is refused. A secret field"
    " (password, TOTP, card number or code, private key, hidden field) is either left blank or"
    " generated:",
    bullets(
        "Left blank: the field starts empty and the user fills it in the bwtidy web UI, where it"
        " shows up in the To fill in list. When the template requires the field, the item shows as"
        " near its template.",
        "Generated: a `login.password` or a hidden field can instead get a value that bw"
        " generates when the item is written (`generate_secrets`, or `generate_password` for a"
        " login's password). Nobody sees it until the user reveals it in the web UI, and nobody"
        " can read it through MCP. It follows the default generator settings the user keeps on"
        " the Settings page.",
    ),
    "An SSH key is refused because its private key cannot come through MCP. Use"
    " propose_convert_item on an item that holds the key instead.",
    PROPOSE_NOTE,
)

END_SESSION = paragraphs("End the bwtidy session, like Log out in the web UI.", END_NOTE)

LOG_OUT_OF_BITWARDEN = paragraphs(
    "End the bwtidy session and log bwtidy's Bitwarden CLI out, like Log out with Also log out"
    " of Bitwarden in the web UI. The next login in the web UI runs the Bitwarden login again,"
    " and bitwarden.com may email a one-time code.",
    END_NOTE,
)

"""The dev vault's accounts and what they hold, built in a stack by scripts/dev-vault setup.

The accounts are registered and their organizations created in the web vault through
Playwright, the rest goes through bw in the app container: the login, the collections, the
fixture under dev/ with its attachments and its trash. Dev credentials only, see dev/README.md.
"""

import base64
import json
import os
import subprocess
import tempfile
from pathlib import Path

from devlib import compose
from devlib.paths import ROOT
from devlib.stacks import Stack

DEV = ROOT / "dev"
PERSONAL_FIXTURE = DEV / "fixture-personal.json"
ORG_FIXTURE = DEV / "fixture-org.json"
SECOND_ORG_FIXTURE = DEV / "fixture-org-club.json"

SERVER_URL = "https://vaultwarden"
EMAIL = "dev@bwtidy.test"
NAME = "Dev"
PASSWORD = "bwtidy-dev-password-1"
ORG = "Family"
COLLECTIONS = ["Shared", "Youth"]
## a second org of the dev account, its collection named like one of Family's on purpose
SECOND_ORG = "Club"
SECOND_COLLECTIONS = ["Shared"]
## each org of the dev account: its collections and the fixture seed imports into it
ORGS = {
    ORG: (COLLECTIONS, ORG_FIXTURE),
    SECOND_ORG: (SECOND_COLLECTIONS, SECOND_ORG_FIXTURE),
}
## a second account with an empty vault and its own org, for importing a backup into
RESTORE_EMAIL = "restore@bwtidy.test"
RESTORE_NAME = "Restore"
RESTORE_ORG = "Restore"
## the restore account also has the dev account's orgs by name, with no collections
RESTORE_ORGS = [RESTORE_ORG, ORG, SECOND_ORG]
## setup's CLI state for the restore account, so the dev account's login stays as it is
RESTORE_APPDATA = "/tmp/dev-vault-restore"

## dev/attachments/<file> goes to the item with this name, two personal and two org items
ATTACHMENTS = {
    "ca-notes.txt": "Internal CA",
    "thinkpad-recovery.txt": "ThinkPad X1",
    "insurance-policy.txt": "Home insurance",
    "youth-laptop-recovery.txt": "Youth laptop",
}
TRASH = ["Old forum account", "Expired license key"]
## where seed copies its files in the app container
SEED_DIR = "/tmp/dev-vault-seed"


## stderr only, bw's stdout can hold a session key or vault data
def failure(command: str, result: subprocess.CompletedProcess) -> str:
    if not result.stderr.strip():
        return f"dev-vault: bw {command} failed with exit {result.returncode} (no error message)"
    return f"dev-vault: bw {command} failed:\n{result.stderr}"


def bw(
    stack: Stack,
    *args: str,
    session: str | None = None,
    check: bool = True,
    appdata: str | None = None,
) -> subprocess.CompletedProcess:
    """Run bw in the stack's app container. The password and session go by environment, not argv.

    appdata names another CLI state directory than the app's own. A failure names bw's stderr
    only, its stdout can hold a session key or vault data.
    """
    env = dict(os.environ, BW_PASSWORD=PASSWORD)
    flags = ["-e", "BW_PASSWORD"]
    if session:
        env["BW_SESSION"] = session
        flags += ["-e", "BW_SESSION"]
    if appdata:
        env["BITWARDENCLI_APPDATA_DIR"] = appdata
        flags += ["-e", "BITWARDENCLI_APPDATA_DIR"]
    result = subprocess.run(
        compose.compose(stack, "exec", "-T", *flags, "app", "bw", *args),
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
    )
    if check and result.returncode != 0:
        raise SystemExit(failure(args[0], result))
    return result


def bw_json(stack: Stack, *args: str, session: str | None = None, appdata: str | None = None):
    return json.loads(bw(stack, *args, session=session, appdata=appdata).stdout)


def browser_page(playwright, port: str):
    ## the browser sees the same https://vaultwarden as the app container, the port goes to the host
    rules = f"--host-resolver-rules=MAP vaultwarden 127.0.0.1:{port}"
    browser = playwright.chromium.launch(args=[rules])
    context = browser.new_context(ignore_https_errors=True)
    ## the web vault must never reach anything but this stack (breach checks, icons, bitwarden.com)
    prefix = f"{SERVER_URL}/"
    context.route(
        "**/*",
        lambda route: route.continue_() if route.request.url.startswith(prefix) else route.abort(),
    )
    return browser, context.new_page()


def register(port: str, email: str, name: str) -> None:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser, page = browser_page(p, port)
        page.goto(f"{SERVER_URL}/#/signup")
        page.fill("#register-start_form_input_email", email)
        page.fill("#register-start_form_input_name", name)
        page.click("button[type=submit]")
        page.wait_for_url("**/finish-signup**")
        page.fill("#input-password-form_new-password", PASSWORD)
        page.fill("#input-password-form_new-password-confirm", PASSWORD)
        page.uncheck("#input-password-form_check-for-breaches")
        page.click("button[type=submit]")
        page.wait_for_url(lambda u: "finish-signup" not in u, timeout=60_000)
        browser.close()


def create_org(port: str, email: str, org: str) -> None:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser, page = browser_page(p, port)
        page.goto(f"{SERVER_URL}/#/login")
        page.fill("#email", email)
        page.get_by_role("button", name="Continue").click()
        page.fill("#masterPassword", PASSWORD)
        page.click("button[type=submit]")
        page.wait_for_url(lambda u: "#/login" not in u, timeout=60_000)
        page.goto(f"{SERVER_URL}/#/create-organization")
        page.get_by_label("Organization name").fill(org)
        page.get_by_role("button", name="Submit").click()
        page.wait_for_url("**/organizations/**", timeout=60_000)
        browser.close()


def ensure_login(
    stack: Stack, port: str, email: str = EMAIL, name: str = NAME, appdata: str | None = None
) -> None:
    status = bw_json(stack, "status", appdata=appdata)
    logged_in = status["status"] != "unauthenticated"
    if logged_in and status.get("serverUrl") == SERVER_URL and status.get("userEmail") == email:
        print(f"dev-vault: bw is logged in as {email}")
        return
    if logged_in:
        bw(stack, "logout", appdata=appdata)
    if status.get("serverUrl") != SERVER_URL:
        bw(stack, "config", "server", SERVER_URL, appdata=appdata)
    login = ["login", email, "--passwordenv", "BW_PASSWORD"]
    result = bw(stack, *login, check=False, appdata=appdata)
    if result.returncode != 0:
        if "Username or password is incorrect" not in result.stdout + result.stderr:
            raise SystemExit(failure("login", result))
        print(f"dev-vault: registering {email}")
        register(port, email, name)
        bw(stack, *login, appdata=appdata)
    print(f"dev-vault: bw logged in as {email}")


def unlock(stack: Stack, appdata: str | None = None) -> str:
    """Unlock bw with the dev password, the session key."""
    return bw(
        stack, "unlock", "--passwordenv", "BW_PASSWORD", "--raw", appdata=appdata
    ).stdout.strip()


def find_org(
    stack: Stack, session: str, name: str = ORG, appdata: str | None = None
) -> dict | None:
    orgs = bw_json(stack, "list", "organizations", session=session, appdata=appdata)
    return next((o for o in orgs if o["name"] == name), None)


def ensure_org(
    stack: Stack,
    port: str,
    session: str,
    email: str = EMAIL,
    name: str = ORG,
    appdata: str | None = None,
) -> dict:
    org = find_org(stack, session, name, appdata)
    if org:
        print(f"dev-vault: org {name} exists")
        return org
    print(f"dev-vault: creating org {name}")
    create_org(port, email, name)
    bw(stack, "sync", session=session, appdata=appdata)
    org = find_org(stack, session, name, appdata)
    if not org:
        raise SystemExit(f"dev-vault: created org {name} in the web vault, but bw does not see it")
    ## the web vault adds one collection on creation, the test vault wants only its own
    org_id = org["id"]
    listed = bw_json(
        stack,
        "list",
        "org-collections",
        "--organizationid",
        org_id,
        session=session,
        appdata=appdata,
    )
    for c in listed:
        bw(
            stack,
            "delete",
            "org-collection",
            c["id"],
            "--organizationid",
            org_id,
            session=session,
            appdata=appdata,
        )
    return org


def ensure_restore_account(stack: Stack, port: str) -> None:
    """The restore account and its orgs, with the vault left empty."""
    try:
        ensure_login(stack, port, RESTORE_EMAIL, RESTORE_NAME, RESTORE_APPDATA)
        session = unlock(stack, RESTORE_APPDATA)
        bw(stack, "sync", session=session, appdata=RESTORE_APPDATA)
        for name in RESTORE_ORGS:
            ensure_org(stack, port, session, RESTORE_EMAIL, name, RESTORE_APPDATA)
    finally:
        compose.run(compose.compose(stack, "exec", "-T", "app", "rm", "-rf", RESTORE_APPDATA))


def ensure_collections(stack: Stack, org: dict, names: list[str], session: str) -> None:
    org_id = org["id"]
    listed = bw_json(stack, "list", "org-collections", "--organizationid", org_id, session=session)
    existing = {c["name"] for c in listed}
    members = bw_json(stack, "list", "org-members", "--organizationid", org_id, session=session)
    me = next(m for m in members if m["email"] == EMAIL)
    for name in names:
        if name in existing:
            print(f"dev-vault: collection {name} of {org['name']} exists")
            continue
        collection = {
            "organizationId": org_id,
            "name": name,
            "externalId": None,
            "groups": [],
            "users": [{"id": me["id"], "readOnly": False, "hidePasswords": False, "manage": True}],
        }
        encoded = base64.b64encode(json.dumps(collection).encode()).decode()
        bw(stack, "create", "org-collection", "--organizationid", org_id, encoded, session=session)
        print(f"dev-vault: created collection {name} of {org['name']}")


def org_fixture(fixture: dict, org_id: str, collections: list[dict]) -> dict:
    """The org fixture with its placeholder ids replaced by the real org and collection ids.

    The server reuses a collection whose id it knows and makes a new one for any other id,
    so the file must carry the ids of the collections setup made.
    """
    real = {c["name"]: c["id"] for c in collections}
    ids = {c["id"]: real[c["name"]] for c in fixture["collections"]}
    result = json.loads(json.dumps(fixture))
    for c in result["collections"]:
        c["id"] = ids[c["id"]]
        c["organizationId"] = org_id
    for item in result["items"]:
        item["organizationId"] = org_id
        item["collectionIds"] = [ids[i] for i in item["collectionIds"]]
    return result


def copy_in(stack: Stack, src: Path, name: str) -> str:
    dest = f"{SEED_DIR}/{name}"
    compose.run(compose.compose(stack, "cp", str(src), f"app:{dest}"), capture_output=True)
    return dest


def vault_empty(stack: Stack, session: str) -> bool:
    return not bw_json(stack, "list", "items", session=session) and not bw_json(
        stack, "list", "items", "--trash", session=session
    )


def seed(stack: Stack, orgs: list[dict], session: str) -> None:
    """Import the personal fixture, then each org's fixture into that org (as bw lists it)."""
    compose.run(compose.compose(stack, "exec", "-T", "app", "mkdir", "-p", SEED_DIR))
    try:
        personal = copy_in(stack, PERSONAL_FIXTURE, PERSONAL_FIXTURE.name)
        bw(stack, "import", "bitwardenjson", personal, session=session)
        print(f"dev-vault: imported {PERSONAL_FIXTURE.name}")

        for org in orgs:
            org_id = org["id"]
            _, org_file_path = ORGS[org["name"]]
            collections = bw_json(
                stack, "list", "org-collections", "--organizationid", org_id, session=session
            )
            fixture = org_fixture(json.loads(org_file_path.read_text()), org_id, collections)
            with tempfile.TemporaryDirectory() as tmp:
                path = Path(tmp) / org_file_path.name
                path.write_text(json.dumps(fixture))
                org_file = copy_in(stack, path, org_file_path.name)
            bw(
                stack,
                "import",
                "bitwardenjson",
                org_file,
                "--organizationid",
                org_id,
                session=session,
            )
            print(f"dev-vault: imported {org_file_path.name} into {org['name']}")

        bw(stack, "sync", session=session)
        items = {i["name"]: i["id"] for i in bw_json(stack, "list", "items", session=session)}
        for file_name, item_name in ATTACHMENTS.items():
            src = copy_in(stack, DEV / "attachments" / file_name, file_name)
            attach = ["create", "attachment", "--file", src, "--itemid", items[item_name]]
            bw(stack, *attach, session=session)
            print(f"dev-vault: attached {file_name} to {item_name}")
        for name in TRASH:
            bw(stack, "delete", "item", items[name], session=session)
            print(f"dev-vault: moved {name} to trash")
        bw(stack, "sync", session=session)
    finally:
        compose.run(compose.compose(stack, "exec", "-T", "app", "rm", "-rf", SEED_DIR))


def from_snapshot_in_place(stack: Stack, env: dict[str, str]) -> bool:
    """True when the stack started from a snapshot and bw is logged in and locked as it was saved.

    The snapshot is what a full setup left, so nothing else needs checking. A full pass of the
    ensure steps would cost a minute (every bw call takes about 3 seconds).
    """
    image = env.get("BWTIDY_VW_IMAGE")
    if not image:
        return False
    status = bw_json(stack, "status")
    if (status["status"], status.get("serverUrl"), status.get("userEmail")) != (
        "locked",
        SERVER_URL,
        EMAIL,
    ):
        print(f"dev-vault: bw is not logged in and locked as in {image}, running the full setup")
        return False
    print(f"dev-vault: started from {image}, bw is logged in as {EMAIL} and locked")
    return True

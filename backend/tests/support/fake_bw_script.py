"""The fake bw executable the backup and write tests install in place of the CLI."""

FAKE_BW = r"""
import io, json, os, sys, time, zipfile

args = sys.argv[1:]
with open(os.environ["FAKE_BW_LOG"], "a") as log:
    log.write(json.dumps(args) + "\n")
if "--nointeraction" not in args:
    sys.stderr.write("no --nointeraction, a real bw would prompt here\n")
    sys.exit(3)
if os.environ.get("BW_SESSION") != os.environ["FAKE_BW_KEY"]:
    sys.stderr.write("Vault is locked.\n")
    sys.exit(1)
fail = os.environ.get("FAKE_BW_FAIL", "")
if fail and fail in " ".join(args):
    sys.stderr.write("Error: forced failure\nsecond line\n")
    sys.exit(1)
if os.environ.get("FAKE_BW_HANG") and "export" in args:
    time.sleep(30)
out = sys.stdout.buffer
if args[:4] == ["--raw", "export", "--format", "zip"]:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        items = json.loads(os.environ.get("FAKE_BW_EXPORT", "[]"))
        z.writestr("data.json", json.dumps({"encrypted": False, "items": items}))
        z.writestr("attachments/ThinkPad X1/thinkpad.txt", b"thinkpad bytes")
    out.write(buf.getvalue())
elif args[:4] == ["--raw", "export", "--format", "json"]:
    org = args[args.index("--organizationid") + 1]
    items = json.loads(os.environ.get("FAKE_BW_ORG_EXPORT", "{}")).get(org, [])
    out.write(json.dumps({"encrypted": False, "items": items, "org": org}).encode())
elif args[:3] == ["--raw", "get", "attachment"]:
    item = args[args.index("--itemid") + 1]
    data = f"bytes of {args[3]} on {item}".encode()
    if os.environ.get("FAKE_BW_SHORT") == args[3]:
        data = data[:-16]
    out.write(data)
elif args[:3] == ["list", "items", "--trash"]:
    out.write(os.environ.get("FAKE_BW_TRASH", "[]").encode())
else:
    sys.stderr.write("unexpected command\n")
    sys.exit(2)
"""

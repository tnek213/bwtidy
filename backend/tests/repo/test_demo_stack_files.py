"""compose.demo.yaml and demo/Dockerfile keep the demo's promises (dev/demo.md).

The stack test (scripts/demo-stack test) proves them on running containers. These checks read
the files alone, so a change that opens a route out of the stack, publishes a second port or
puts demo code into the normal image fails scripts/ci at once.
"""

import re
from pathlib import Path

from bwtidy.gateway.settings import DEFAULT_UID_BASE

ROOT = Path(__file__).resolve().parents[3]
COMPOSE = (ROOT / "compose.demo.yaml").read_text()
DEMO_DOCKERFILE = (ROOT / "demo" / "Dockerfile").read_text()
DOCKERFILE = (ROOT / "Dockerfile").read_text()
SERVICES = ["init", "vaultwarden", "controller", "demo", "edge"]


def blocks(text: str, section: str) -> dict[str, str]:
    """The entries of a top-level section of a compose file, each with its lines as text."""
    found: dict[str, list[str]] = {}
    inside = False
    name = ""
    for line in text.splitlines():
        if re.match(r"\S", line) and not line.startswith("#"):
            inside = line.split(":")[0] == section
            continue
        if not inside:
            continue
        entry = re.fullmatch(r"  ([\w-]+):.*", line)
        if entry:
            name = entry[1]
            found[name] = []
        elif name:
            found[name].append(line)
    return {name: "\n".join(lines) + "\n" for name, lines in found.items()}


def listed(block: str, key: str) -> list[str]:
    """The items of the list under a service's key."""
    match = re.search(rf"^    {key}:\n((?:      .*\n)*)", block, re.MULTILINE)
    if match is None:
        return []
    return [line.strip()[2:] for line in match[1].splitlines() if line.strip().startswith("- ")]


def test_the_stack_has_the_five_services_of_the_design():
    assert list(blocks(COMPOSE, "services")) == SERVICES


def test_only_the_public_network_has_a_route_out():
    networks = blocks(COMPOSE, "networks")
    assert set(networks) == {"back", "front", "public"}
    assert "internal: true" in networks["back"]
    assert "internal: true" in networks["front"]
    assert "internal" not in networks["public"]


def test_only_the_edge_is_on_the_public_network():
    services = blocks(COMPOSE, "services")
    assert {name: listed(block, "networks") for name, block in services.items()} == {
        "init": [],
        "vaultwarden": ["back"],
        "controller": ["back"],
        "demo": ["back", "front"],
        "edge": ["front", "public"],
    }
    assert "    network_mode: none\n" in services["init"]
    for name in ("vaultwarden", "controller", "demo", "edge"):
        assert "network_mode" not in services[name], name


def test_only_the_edge_publishes_a_port_and_on_loopback_by_default():
    services = blocks(COMPOSE, "services")
    assert [name for name, block in services.items() if "ports:" in block] == ["edge"]
    assert listed(services["edge"], "ports") == [
        '"${DEMO_BIND:-127.0.0.1}:${DEMO_PORT:-8770}:${DEMO_EDGE_PORT:-80}"'
    ]


def test_the_only_allowed_server_is_the_stacks_own_and_no_setting_changes_it():
    demo = blocks(COMPOSE, "services")["demo"]
    assert "      BWTIDY_ALLOWED_SERVERS: https://vaultwarden\n" in demo
    assert COMPOSE.count("BWTIDY_ALLOWED_SERVERS") == 1
    assert "DEMO_SERVER" not in COMPOSE


def test_every_container_is_read_only_without_capabilities_it_does_not_need():
    services = blocks(COMPOSE, "services")
    for name, block in services.items():
        assert "    read_only: true\n" in block, name
        assert listed(block, "cap_drop") == ["ALL"], name
        assert listed(block, "security_opt") == ["no-new-privileges:true"], name
        assert "privileged" not in block, name
    bwtidy = blocks((ROOT / "compose.yaml").read_text(), "services")["bwtidy"]
    assert listed(services["demo"], "cap_add") == listed(bwtidy, "cap_add")
    ## init gives the secrets and the ledger their owners, and needs nothing else
    assert listed(services["init"], "cap_add") == ["CHOWN"]
    for name in ("vaultwarden", "controller"):
        assert listed(services[name], "cap_add") == [], name


def test_the_controller_runs_as_its_own_user_of_the_demo_image():
    services = blocks(COMPOSE, "services")
    user = re.search(r'^    user: "(\d+):(\d+)"$', services["controller"], re.MULTILINE)
    assert user and user.groups() == ("10100", "10100")
    ## below bwtidy's accounts, so no worker ever runs as the controller
    assert int(user[1]) < DEFAULT_UID_BASE
    ## the others keep root: init sets owners, the gateway starts the workers as their uids
    for name in ("init", "vaultwarden", "demo"):
        assert "    user:" not in services[name], name
    assert "groupadd --system --gid 10100 controller" in DEMO_DOCKERFILE
    assert "useradd --system --uid 10100 --gid 10100" in DEMO_DOCKERFILE
    assert "chown 10100:10100 /ledger" in DEMO_DOCKERFILE
    init = (ROOT / "demo" / "src" / "bwtidy_demo" / "init.py").read_text()
    assert "\nCONTROLLER_UID = 10100\n" in init
    assert "\nCONTROLLER_GID = 10100\n" in init


def test_init_fixes_the_ledger_volume_the_controller_writes():
    services = blocks(COMPOSE, "services")
    assert "- ledger:/ledger\n" in services["init"]
    assert "- ledger:/ledger\n" in services["controller"]


def test_the_edge_mounts_no_secret_and_only_init_writes_them():
    services = blocks(COMPOSE, "services")
    assert "secrets" not in services["edge"]
    assert "- secrets:/secrets\n" in services["init"]
    for name in ("vaultwarden", "controller", "demo"):
        assert "- secrets:/secrets:ro\n" in services[name], name


def test_vaultwarden_is_the_version_of_the_dev_stack():
    dev = re.search(r"vaultwarden/server:([\w.]+)", (ROOT / "compose.dev.yaml").read_text())
    demo = re.search(r"image: vaultwarden/server:([\w.]+)@sha256:", COMPOSE)
    assert dev and demo
    assert demo[1] == dev[1]


def test_the_demo_image_is_built_on_the_normal_image_with_uv_alone():
    assert "\nARG BWTIDY_IMAGE=docker.io/tnek213/bwtidy:main\n" in DEMO_DOCKERFILE
    assert "\nFROM ${BWTIDY_IMAGE}\n" in DEMO_DOCKERFILE
    code = [line for line in DEMO_DOCKERFILE.splitlines() if not line.startswith("#")]
    ## the normal image has no pip
    assert not [line for line in code if re.search(r"(?<!uv )\bpip3?\b", line)]
    ## the base's user stays: the gateway needs root to start each worker as its account's uid
    assert not [line for line in code if line.startswith("USER")]
    assert "--locked" in DEMO_DOCKERFILE
    assert "--no-deps" in DEMO_DOCKERFILE


def test_both_dockerfiles_pin_the_same_syntax_and_uv():
    for pattern in (r"# syntax=\S+", r"FROM ghcr\.io/astral-sh/uv:\S+ AS uv"):
        normal = re.search(pattern, DOCKERFILE)
        demo = re.search(pattern, DEMO_DOCKERFILE)
        assert normal and demo, pattern
        assert demo[0] == normal[0]


def test_the_normal_image_holds_no_demo_code():
    assert "demo" not in DOCKERFILE.lower()
    sources = (ROOT / "backend" / "src").rglob("*.py")
    assert [str(path) for path in sources if "bwtidy_demo" in path.read_text()] == []


def test_every_service_has_a_memory_and_a_process_limit_from_a_documented_setting():
    docs = (ROOT / "dev" / "demo.md").read_text()
    for name, block in blocks(COMPOSE, "services").items():
        upper = name.upper()
        memory = re.search(r"^    mem_limit: \$\{(DEMO_MEM_\w+):-([^}]+)\}$", block, re.MULTILINE)
        swap = re.search(r"^    memswap_limit: \$\{(DEMO_MEM_\w+):-([^}]+)\}$", block, re.MULTILINE)
        pids = re.search(r"^    pids_limit: \$\{(DEMO_PIDS_\w+):-(\d+)\}$", block, re.MULTILINE)
        assert memory and swap and pids, name
        assert memory[1] == f"DEMO_MEM_{upper}", name
        assert pids[1] == f"DEMO_PIDS_{upper}", name
        assert swap.groups() == memory.groups(), name
        for variable in (memory[1], pids[1]):
            assert f"`{variable}`" in docs, variable

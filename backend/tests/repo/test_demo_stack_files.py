"""compose.demo.yaml and demo/Dockerfile keep the demo's promises (dev/demo.md).

The stack test (scripts/demo-stack test) proves them on running containers. These checks read
the files alone, so a change that opens a route out of the stack, publishes a second port or
puts demo code into the normal image fails scripts/ci at once.
"""

import re
from pathlib import Path

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


def test_the_public_bridge_has_a_fixed_name_that_scripts_demo_egress_accepts():
    public = blocks(COMPOSE, "networks")["public"]
    match = re.search(
        r"^      com\.docker\.network\.bridge\.name: \$\{DEMO_PUBLIC_BRIDGE:-([^}]+)\}$",
        public,
        re.MULTILINE,
    )
    assert match, public
    ## the kernel's limit for an interface name, and what scripts/demo-egress takes
    assert re.fullmatch(r"[A-Za-z0-9._-]{1,15}", match[1])


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
    for name in ("init", "vaultwarden", "controller"):
        assert listed(services[name], "cap_add") == [], name


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

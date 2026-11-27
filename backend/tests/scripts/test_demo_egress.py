"""scripts/demo-egress: the chains it writes for the demo's public network, and its refusals."""

import pytest

BRIDGE = "br-bwtidy-demo"
FORWARD = "BWTIDY-FWD-br-bwtidy-demo"
HOST = "BWTIDY-IN-br-bwtidy-demo"
ESTABLISHED = "-m conntrack --ctstate ESTABLISHED,RELATED -j RETURN"
NON_PUBLIC = [
    "0.0.0.0/8",
    "10.0.0.0/8",
    "100.64.0.0/10",
    "127.0.0.0/8",
    "169.254.0.0/16",
    "172.16.0.0/12",
    "192.168.0.0/16",
    "224.0.0.0/4",
    "240.0.0.0/4",
]
## a resolver on the local network and one on the host's own address
LAN_DNS = "192.168.1.1"
HOST_DNS = "172.30.0.1"


@pytest.fixture
def egress(load_script, monkeypatch):
    module = load_script("demo-egress")
    monkeypatch.setattr(module, "is_host_address", lambda address: address == HOST_DNS)
    return module


class FakeIptables:
    """The filter table as iptables changes it: every call recorded, a wrong one fails."""

    def __init__(self, docker: bool = True) -> None:
        self.chains: dict[str, list[tuple[str, ...]]] = {"INPUT": [("-j", "ACCEPT")]}
        if docker:
            self.chains["DOCKER-USER"] = [("-j", "RETURN")]
        self.calls: list[list[str]] = []

    def __call__(self, args: list[str]) -> tuple[int, str]:
        self.calls.append(args)
        op, chain, rest = args[0], args[1], tuple(args[2:])
        if op == "-N":
            if chain in self.chains:
                return 1, "Chain already exists."
            self.chains[chain] = []
            return 0, ""
        if chain not in self.chains:
            return 1, "No chain/target/match by that name."
        rules = self.chains[chain]
        if op == "-S":
            return 0, ""
        if op == "-F":
            rules.clear()
        elif op == "-X":
            if rules or any(rule[-1] == chain for other in self.chains.values() for rule in other):
                return 1, "Directory not empty."
            del self.chains[chain]
        elif op == "-A":
            rules.append(rest)
        elif op == "-I":
            rules.insert(int(rest[0]) - 1, rest[1:])
        elif op == "-C":
            return (0, "") if rest in rules else (1, "Bad rule.")
        elif op == "-D":
            if rest not in rules:
                return 1, "Bad rule."
            rules.remove(rest)
        else:
            return 2, f"unknown option {op}"
        return 0, ""

    def jumps(self, hook: str, chain: str) -> int:
        return self.chains[hook].count(("-i", BRIDGE, "-j", chain))


def lines(prefix: str, rules: list[str]) -> list[str]:
    return [f"iptables -w {prefix} {rule}" for rule in rules]


def dns(address: str) -> list[str]:
    return [f"-d {address} -p {protocol} --dport 53 -j RETURN" for protocol in ("udp", "tcp")]


GUARDS = [f"iptables -w -I {hook} 1 -i {BRIDGE} -j DROP" for hook in ("DOCKER-USER", "INPUT")]
JUMPS = [
    f"iptables -w -I DOCKER-USER 1 -i {BRIDGE} -j {FORWARD}",
    f"iptables -w -I INPUT 1 -i {BRIDGE} -j {HOST}",
]
UNGUARDS = [f"iptables -w -D {hook} -i {BRIDGE} -j DROP" for hook in ("DOCKER-USER", "INPUT")]


def test_print_in_mode_none_drops_all_but_the_answers(egress, capsys):
    assert egress.main(["print", "--bridge", BRIDGE, "--mode", "none"]) == 0
    assert capsys.readouterr().out.splitlines() == [
        *GUARDS,
        f"iptables -w -N {FORWARD}",
        *lines(f"-A {FORWARD}", [ESTABLISHED, "-j DROP"]),
        f"iptables -w -N {HOST}",
        *lines(f"-A {HOST}", [ESTABLISHED, "-j DROP"]),
        *JUMPS,
        *UNGUARDS,
    ]


def test_print_in_mode_vpn_drops_the_ranges_that_are_not_public_before_any_allow(egress, capsys):
    args = ["print", "--bridge", BRIDGE, "--mode", "vpn"]
    assert egress.main([*args, "--allow-dns", LAN_DNS, "--allow-dns", HOST_DNS]) == 0
    forward = [
        ESTABLISHED,
        *dns(LAN_DNS),
        *dns(HOST_DNS),
        *(f"-d {cidr} -j DROP" for cidr in NON_PUBLIC),
        "-p udp -j RETURN",
        "-p tcp --dport 80 -j RETURN",
        "-p tcp --dport 443 -j RETURN",
        "-p tcp --dport 53 -j RETURN",
        "-j DROP",
    ]
    assert capsys.readouterr().out.splitlines() == [
        *GUARDS,
        f"iptables -w -N {FORWARD}",
        *lines(f"-A {FORWARD}", forward),
        f"iptables -w -N {HOST}",
        *lines(f"-A {HOST}", [ESTABLISHED, *dns(HOST_DNS), "-j DROP"]),
        *JUMPS,
        *UNGUARDS,
    ]


def test_print_in_mode_none_lets_an_allowed_resolver_through(egress, capsys):
    args = ["print", "--bridge", BRIDGE, "--mode", "none", "--allow-dns", LAN_DNS]
    assert egress.main(args) == 0
    out = capsys.readouterr().out.splitlines()
    assert lines(f"-A {FORWARD}", [ESTABLISHED, *dns(LAN_DNS), "-j DROP"]) == [
        line for line in out if f"-A {FORWARD}" in line
    ]
    assert lines(f"-A {HOST}", [ESTABLISHED, "-j DROP"]) == [
        line for line in out if f"-A {HOST}" in line
    ]


def test_the_list_holds_the_ranges_of_the_design(egress):
    assert egress.NON_PUBLIC == NON_PUBLIC


def test_the_chain_names_fit_iptables_for_the_longest_bridge_name(egress):
    assert all(len(chain) <= 28 for chain in egress.chains("a" * 15))
    assert egress.chains(BRIDGE) == (FORWARD, HOST)


@pytest.fixture
def host(egress, monkeypatch):
    """A root shell with iptables, the fake behind it."""
    fake = FakeIptables()
    monkeypatch.setattr(egress, "run_iptables", fake)
    monkeypatch.setattr(egress.shutil, "which", lambda name: f"/usr/sbin/{name}")
    monkeypatch.setattr(egress.os, "geteuid", lambda: 0)
    return fake


def test_a_second_apply_keeps_the_same_rules_and_one_jump_each(egress, host):
    args = ["apply", "--bridge", BRIDGE, "--mode", "vpn", "--allow-dns", LAN_DNS]
    assert egress.main(args) == 0
    first = {name: list(rules) for name, rules in host.chains.items()}
    assert egress.main(args) == 0
    assert host.chains == first
    assert host.jumps("DOCKER-USER", FORWARD) == 1
    assert host.jumps("INPUT", HOST) == 1
    assert host.chains["DOCKER-USER"][0] == ("-i", BRIDGE, "-j", FORWARD)
    assert host.chains["INPUT"][0] == ("-i", BRIDGE, "-j", HOST)
    assert ("-i", BRIDGE, "-j", "DROP") not in host.chains["DOCKER-USER"]
    assert ("-i", BRIDGE, "-j", "DROP") not in host.chains["INPUT"]
    assert host.chains[FORWARD][-1] == ("-j", "DROP")


def test_apply_in_another_mode_replaces_the_rules(egress, host):
    assert egress.main(["apply", "--bridge", BRIDGE, "--mode", "vpn"]) == 0
    assert egress.main(["apply", "--bridge", BRIDGE, "--mode", "none"]) == 0
    assert host.chains[FORWARD] == [tuple(ESTABLISHED.split()), ("-j", "DROP")]
    assert host.jumps("DOCKER-USER", FORWARD) == 1


def test_apply_closes_the_bridge_before_it_flushes_a_chain(egress, host):
    assert egress.main(["apply", "--bridge", BRIDGE, "--mode", "none"]) == 0
    host.calls.clear()
    assert egress.main(["apply", "--bridge", BRIDGE, "--mode", "vpn"]) == 0
    flush = host.calls.index(["-F", FORWARD])
    assert host.calls[:flush].count(["-I", "DOCKER-USER", "1", "-i", BRIDGE, "-j", "DROP"]) == 1
    assert host.calls[:flush].count(["-I", "INPUT", "1", "-i", BRIDGE, "-j", "DROP"]) == 1


def test_remove_leaves_no_chain_and_no_jump(egress, host):
    before = {name: list(rules) for name, rules in host.chains.items()}
    assert egress.main(["apply", "--bridge", BRIDGE, "--mode", "vpn"]) == 0
    assert egress.main(["remove", "--bridge", BRIDGE]) == 0
    assert host.chains == before
    assert egress.main(["remove", "--bridge", BRIDGE]) == 0


@pytest.mark.parametrize(
    ("bridge", "message"),
    [
        ("br-bwtidy-demo-x", "longer than 15 characters"),
        ("br bwtidy", "only letters, digits, dot, dash and underscore"),
        ("br;reboot", "only letters, digits, dot, dash and underscore"),
    ],
)
def test_a_bad_bridge_name_is_refused(egress, host, capsys, bridge, message):
    assert egress.main(["apply", "--bridge", bridge, "--mode", "none"]) == 1
    assert message in capsys.readouterr().err
    assert host.calls == []


def test_an_address_that_is_not_ipv4_is_refused(egress, host, capsys):
    args = ["apply", "--bridge", BRIDGE, "--mode", "vpn", "--allow-dns", "::1"]
    assert egress.main(args) == 1
    assert "not an IPv4 address" in capsys.readouterr().err


def test_without_iptables_it_says_so(egress, host, monkeypatch, capsys):
    monkeypatch.setattr(egress.shutil, "which", lambda name: None)
    assert egress.main(["apply", "--bridge", BRIDGE, "--mode", "none"]) == 1
    assert "iptables is not installed" in capsys.readouterr().err
    assert host.calls == []


def test_not_as_root_it_says_so(egress, host, monkeypatch, capsys):
    monkeypatch.setattr(egress.os, "geteuid", lambda: 1000)
    assert egress.main(["remove", "--bridge", BRIDGE]) == 1
    assert "run it as root" in capsys.readouterr().err
    assert host.calls == []


def test_before_docker_has_started_it_says_so(egress, host, capsys):
    del host.chains["DOCKER-USER"]
    assert egress.main(["apply", "--bridge", BRIDGE, "--mode", "none"]) == 1
    assert "DOCKER-USER does not exist yet: start Docker first" in capsys.readouterr().err
    assert [call for call in host.calls if call[0] != "-S"] == []


def test_a_failed_iptables_call_names_the_command(egress, host, monkeypatch, capsys):
    def failing(args):
        return (4, "Permission denied") if args[0] == "-N" else host(args)

    monkeypatch.setattr(egress, "run_iptables", failing)
    assert egress.main(["apply", "--bridge", BRIDGE, "--mode", "none"]) == 1
    assert f"iptables -N {FORWARD} failed: Permission denied" in capsys.readouterr().err
    ## a run that stops halfway leaves the bridge closed, not open
    assert host.chains["DOCKER-USER"][0] == ("-i", BRIDGE, "-j", "DROP")

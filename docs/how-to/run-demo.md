# Run a public demo

Most people never need this page. It is for someone who wants to let others
try bwtidy without installing it.

The demo serves a throwaway bwtidy with a Vaultwarden of its own, and it
never touches a real vault. Every visitor gets a fresh vault that is deleted
when the visit ends.

The design and every setting are in the
[demo notes](https://github.com/tnek213/bwtidy/blob/main/dev/demo.md) of the
repo. This page lists what every setup must do and shows three setups as
examples. Your host and your proxy may differ, so treat the examples as
starting points.

## What every demo needs

- A host with Docker, Compose and buildx 0.17 or later. The default limit of
  6 visitors at once needs a machine with 4 GB of memory, as
  [Memory](https://github.com/tnek213/bwtidy/blob/main/dev/demo.md#memory)
  shows. A bigger machine allows more visitors.
- A pinned bwtidy. Set `BWTIDY_IMAGE` to a short-commit tag or a release and
  point `DEMO_BUILD_CONTEXT` at the same commit or tag, so the demo runs a
  bwtidy that somebody chose. To update, set the new values and run
  `docker compose -f compose.demo.yaml up -d --build` again. Every running
  demo ends then and the ledger stays. See
  [Run it](https://github.com/tnek213/bwtidy/blob/main/dev/demo.md#run-it).
- HTTPS in front of the edge. Set `DEMO_HOST` to the public name and
  `DEMO_SECURE_COOKIES=1`, as the cookies and MCP's host check need them.
  See [On a VPS](https://github.com/tnek213/bwtidy/blob/main/dev/demo.md#on-a-vps).
- The visitor's own address at the edge's rate limits. When a proxy or a
  tunnel sits in front of the edge, set `DEMO_TRUSTED_PROXIES` to the address
  the edge sees it from. Without it all visitors share one limit. See
  [Behind a reverse proxy](https://github.com/tnek213/bwtidy/blob/main/dev/demo.md#behind-a-reverse-proxy).
- No port reachable from outside except through that HTTPS front. Leave
  `DEMO_BIND` at loopback, or publish no port at all when a tunnel carries
  the edge. See
  [Run it](https://github.com/tnek213/bwtidy/blob/main/dev/demo.md#run-it).
- The egress rules, applied at every boot. Install `scripts/demo-egress` and
  its systemd unit with mode `none` for the edge alone or `vpn` for a VPN
  sidecar. They keep the demo's public network away from the host and the
  local network. See
  [Limit the way out](https://github.com/tnek213/bwtidy/blob/main/dev/demo.md#limit-the-way-out).
- The memory and process limits of `compose.demo.yaml`, which stay on. When
  you raise `DEMO_MAX`, raise `DEMO_MEM_DEMO`, `DEMO_PIDS_DEMO` and
  `DEMO_MEM_VAULTWARDEN` with it by the amounts the notes give. See
  [Limits and timeouts](https://github.com/tnek213/bwtidy/blob/main/dev/demo.md#limits-and-timeouts).
- A check that it works. Open the public address and click Start demo.
  Create an MCP token in the vault as
  [Connect an MCP client](mcp.md) describes and call one tool. Then end the
  demo from the app and see that the vault is gone. The logs are
  `docker compose -f compose.demo.yaml logs -f demo controller edge` and the
  controller's counts are in
  [Run it](https://github.com/tnek213/bwtidy/blob/main/dev/demo.md#run-it).
  `docker compose -f compose.demo.yaml down` stops the demo and keeps the
  ledger, and `down --volumes` is for a test stack only, as it forgets which
  ids were used.

## Example setups

### A VPS with its own reverse proxy

The demo and a reverse proxy run on one machine. The demo stays on loopback
and the proxy holds the certificate. Besides the checklist you need a name in
DNS that points at the machine and ports 80 and 443 open.

Caddy needs three lines and gets the certificate itself.

```text
demo.example.org {
    reverse_proxy 127.0.0.1:8770
}
```

An nginx server block with a certbot certificate works as well. Set
`DEMO_TRUSTED_PROXIES` to the address the edge sees the proxy from. Use mode
`none` for the egress rules. The notes hold the
[nginx block](https://github.com/tnek213/bwtidy/blob/main/dev/demo.md#on-a-vps)
and the whole procedure.

### A private host reached through a tunnel

The demo host opens no port. A VPN or tunnel container, the sidecar, carries
the edge's network, and a small public machine runs the proxy that reaches
the edge through the tunnel.

- The stack publishes no port and runs with an override file that makes the
  edge share the sidecar's network.
- The proxy machine holds the certificate and sets `X-Forwarded-For`.
- `DEMO_TRUSTED_PROXIES` is the proxy's tunnel address as the edge sees it.
- The egress rules use mode `vpn`.

The override file and the sidecar's privileges are in
[Behind a reverse proxy](https://github.com/tnek213/bwtidy/blob/main/dev/demo.md#behind-a-reverse-proxy).

### A demo only you can reach

The edge stays on loopback and an SSH tunnel brings its port to your laptop.
No HTTPS and no proxy are needed, so leave `DEMO_SECURE_COOKIES` empty.

```sh
ssh -L 8770:127.0.0.1:8770 HOST
```

The demo is then at `http://localhost:8770`. The notes explain
[why a tunnel beats a hidden name](https://github.com/tnek213/bwtidy/blob/main/dev/demo.md#private-through-an-ssh-tunnel).

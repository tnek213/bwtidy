#!/bin/sh
## Run by the nginx image's entrypoint before nginx starts (dev/demo.md, The edge).
## Writes the real-ip config for the reverse proxies named in DEMO_TRUSTED_PROXIES, so the
## limits count the visitor and not the proxy. With none named it writes nothing, and
## X-Forwarded-For is never trusted.
set -eu

dir="${NGINX_CONF_DIR:-/etc/nginx/conf.d}"
out="$dir/00-real-ip.conf"
rm -f "$out"

[ -n "${DEMO_TRUSTED_PROXIES:-}" ] || exit 0

{
    for proxy in $DEMO_TRUSTED_PROXIES; do
        echo "set_real_ip_from $proxy;"
    done
    echo "real_ip_header X-Forwarded-For;"
    echo "real_ip_recursive on;"
} > "$out"

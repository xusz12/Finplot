#!/bin/sh
set -eu

script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
project_dir=$(CDPATH= cd -- "$script_dir/.." && pwd)
uvicorn_bin="$project_dir/.venv/bin/uvicorn"

if [ -z "${LEDGER_DB:-}" ]; then
    echo "LEDGER_DB must point to a ledger database" >&2
    exit 2
fi
if [ ! -x "$uvicorn_bin" ]; then
    echo "missing $uvicorn_bin; create the virtual environment and install backend/requirements.txt first" >&2
    exit 2
fi

mode=${1:-loopback}
case "$mode" in
    loopback)
        bind_host=127.0.0.1
        default_port=8766
        unset FINPLOT_DIRECT_HOST FINPLOT_DIRECT_PORT
        ;;
    tailscale-beta|tailscale-stable)
        if ! command -v tailscale >/dev/null 2>&1; then
            echo "tailscale command is unavailable" >&2
            exit 2
        fi
        bind_host=$(tailscale ip -4 2>/dev/null) || {
            echo "Tailscale is not running or has no IPv4 address" >&2
            exit 2
        }
        case "$bind_host" in
            *'
'*|'')
                echo "Tailscale returned an invalid IPv4 address" >&2
                exit 2
                ;;
        esac
        if ! "$project_dir/.venv/bin/python" - "$bind_host" <<'PY'
import ipaddress
import sys

try:
    address = ipaddress.ip_address(sys.argv[1])
except ValueError:
    raise SystemExit(1)
if not isinstance(address, ipaddress.IPv4Address) or address not in ipaddress.ip_network("100.64.0.0/10"):
    raise SystemExit(1)
PY
        then
            echo "Tailscale returned an invalid IPv4 address" >&2
            exit 2
        fi
        default_port=8775
        [ "$mode" = tailscale-stable ] && default_port=8766
        export FINPLOT_DIRECT_HOST=$bind_host
        unset FINPLOT_PUBLIC_HOST
        ;;
    *)
        echo "usage: $0 [loopback|tailscale-beta|tailscale-stable]" >&2
        exit 2
        ;;
esac

if [ "${FINPLOT_PORT+x}" = x ]; then
    port=$FINPLOT_PORT
else
    port=$default_port
fi
case "$port" in
    ''|*[!0-9]*)
        echo "FINPLOT_PORT must be a numeric TCP port" >&2
        exit 2
        ;;
esac
if [ "$port" -lt 1 ] || [ "$port" -gt 65535 ]; then
    echo "FINPLOT_PORT must be between 1 and 65535" >&2
    exit 2
fi

if [ "$mode" != loopback ]; then
    export FINPLOT_DIRECT_PORT=$port
fi

# Empty by default: only loopback Host/Origin requests are accepted unless the
# caller explicitly supplies the one Tailscale Serve hostname.
export FINPLOT_PUBLIC_HOST=${FINPLOT_PUBLIC_HOST:-}

if [ "${FINPLOT_DRY_RUN:-}" = 1 ]; then
    printf 'bind_host=%s\nport=%s\n' "$bind_host" "$port"
    exit 0
fi

cd "$project_dir"
exec "$uvicorn_bin" backend.app.main:app \
    --host "$bind_host" \
    --port "$port" \
    --no-access-log

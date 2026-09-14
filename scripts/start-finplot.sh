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

port=${FINPLOT_PORT:-8766}
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

# Empty by default: only loopback Host/Origin requests are accepted unless the
# caller explicitly supplies the one Tailscale Serve hostname.
export FINPLOT_PUBLIC_HOST=${FINPLOT_PUBLIC_HOST:-}

cd "$project_dir"
exec "$uvicorn_bin" backend.app.main:app \
    --host 127.0.0.1 \
    --port "$port" \
    --no-access-log

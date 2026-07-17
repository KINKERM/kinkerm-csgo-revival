#!/usr/bin/env bash
# One-command start for the CS:GO Revival inventory server (Linux/macOS).
#
# Usage:
#   ./start-server.sh                 # listen on 0.0.0.0:8787
#   ./start-server.sh --port 9000     # custom port
#
# Any arguments are passed straight through to revival_server.py.
set -euo pipefail

cd "$(dirname "$0")"

# Pick a python
if command -v python3 >/dev/null 2>&1; then
    PY=python3
elif command -v python >/dev/null 2>&1; then
    PY=python
else
    echo "ERROR: Python 3 is not installed. Install it from https://www.python.org/downloads/" >&2
    exit 1
fi

# First-run convenience: if no catalog yet, fall back to the sample so the
# server can start. Replace it with a generated one via build_catalog.py.
if [ ! -f data/catalog.json ] && [ -f data/catalog.sample.json ]; then
    echo "[start-server] no data/catalog.json found - using the sample catalog for now."
    echo "[start-server] run build_catalog.py against your items_game.txt for the full list."
    cp data/catalog.sample.json data/catalog.json
fi

exec "$PY" revival_server.py --host 0.0.0.0 --port 8787 "$@"

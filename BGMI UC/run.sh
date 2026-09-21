#!/usr/bin/env bash
# Start the UC store. Extra arguments are forwarded to server.py,
# e.g.   ./run.sh --port 9000 --open
set -e
cd "$(dirname "$0")"
PYTHON="${PYTHON:-python3}"
if ! command -v "$PYTHON" >/dev/null 2>&1; then
  PYTHON=python
fi
exec "$PYTHON" server.py "$@"

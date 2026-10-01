#!/bin/sh
# Use this checkout's configured interpreter, independent of caller cwd/PATH.
# Never load env files, install dependencies, or guess a shared interpreter.
set -eu

project_root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd -P) || {
    printf '%s\n' '[UNVERIFIED] cannot locate project root' >&2
    exit 1
}
project_python="$project_root/.venv/bin/python"
if [ ! -x "$project_python" ]; then
    printf '%s\n' '[UNVERIFIED] missing executable .venv/bin/python; initialize this checkout per docs/development/local-development.md' >&2
    exit 1
fi
cd -- "$project_root"
exec "$project_python" "$@"

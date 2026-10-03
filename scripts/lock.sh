#!/bin/sh
# Regenerates the Python lockfiles from pyproject.toml.
#   scripts/lock.sh            keep the current versions where pyproject.toml still allows them
#   scripts/lock.sh --upgrade  move everything to the newest allowed versions
# Needs uv (https://docs.astral.sh/uv/). The locks are universal, so they install on Linux, macOS and Windows.
set -eu
cd "$(dirname "$0")/.."
export UV_CUSTOM_COMPILE_COMMAND="scripts/lock.sh"
uv pip compile pyproject.toml --universal --python-version 3.12 --generate-hashes --quiet "$@" -o requirements.lock
uv pip compile pyproject.toml --extra dev --universal --python-version 3.12 --quiet "$@" -o requirements-dev.lock

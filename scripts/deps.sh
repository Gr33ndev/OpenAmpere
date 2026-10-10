#!/bin/sh
# SPDX-FileCopyrightText: Copyright the OpenAmpere contributors
# SPDX-License-Identifier: MIT
# Updates every generated dependency file in one go, so a commit that changes dependencies is complete and CI is green
# right away (otherwise the "Dependency files" workflow has to commit them afterwards):
#   - requirements.lock / requirements-dev.lock (scripts/lock.sh, arguments are passed on, e.g. --upgrade)
#   - THIRD_PARTY_LICENSES.md and web/public/third-party-licenses.json (scripts/third_party_licenses.py)
# The license list is generated in a throwaway environment with exactly the locked versions, nothing else.
# Needs uv (https://docs.astral.sh/uv/) and npm.
set -eu
cd "$(dirname "$0")/.."
scripts/lock.sh "$@"
env_dir=$(mktemp -d)
trap 'rm -rf "$env_dir"' EXIT
uv venv --quiet --python 3.12 "$env_dir"
uv pip install --quiet --python "$env_dir/bin/python" -r requirements-dev.lock
uv pip install --quiet --python "$env_dir/bin/python" --no-deps -e .
(cd web && npm ci --silent)
"$env_dir/bin/python" scripts/third_party_licenses.py
git status --short requirements.lock requirements-dev.lock THIRD_PARTY_LICENSES.md web/public/third-party-licenses.json

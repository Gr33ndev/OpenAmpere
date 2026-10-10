#!/bin/sh
# SPDX-FileCopyrightText: Copyright the OpenAmpere contributors
# SPDX-License-Identifier: MIT
# Regenerates the Python lockfiles, all with hashes so pip installs exactly these files (--require-hashes):
#   requirements.lock        the app (from pyproject.toml), installed in the Docker image
#   requirements-dev.lock    the app plus the dev tools, for CI
#   requirements-build.lock  the build backend from [build-system] in pyproject.toml, for the Docker image
#   tests_ha/requirements.lock  the Home Assistant test environment (from tests_ha/requirements.txt, Python 3.14.2+ like Home Assistant)
#   scripts/lock.sh            keep the current versions where pyproject.toml still allows them
#   scripts/lock.sh --upgrade  move everything to the newest allowed versions
# Needs uv (https://docs.astral.sh/uv/). The locks are universal, so they install on Linux, macOS and Windows.
set -eu
cd "$(dirname "$0")/.."
export UV_CUSTOM_COMPILE_COMMAND="scripts/lock.sh"
uv pip compile pyproject.toml --universal --python-version 3.12 --generate-hashes --quiet "$@" -o requirements.lock
uv pip compile pyproject.toml --extra dev --universal --python-version 3.12 --generate-hashes --quiet "$@" \
  -o requirements-dev.lock
python3 -c 'import tomllib; print("\n".join(tomllib.load(open("pyproject.toml", "rb"))["build-system"]["requires"]))' |
  uv pip compile - --universal --python-version 3.12 --generate-hashes --quiet "$@" -o requirements-build.lock
uv pip compile tests_ha/requirements.txt --universal --python-version 3.14.2 --generate-hashes --quiet "$@" \
  -o tests_ha/requirements.lock

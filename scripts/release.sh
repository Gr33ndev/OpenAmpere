#!/bin/sh
# Prepares a release: sets the version, adds it to the changelog, commits both and creates a signed tag.
#   scripts/release.sh 0.2.0
# Then push both: git push origin main v0.2.0
# The "Release" workflow builds the Docker image and creates the GitHub release with notes.
set -eu
cd "$(dirname "$0")/.."
version=${1:?Version angeben, z. B. scripts/release.sh 0.2.0}
case "$version" in v*) version=${version#v} ;; esac
[ -z "$(git status --porcelain)" ] || { echo "Erst alle Änderungen committen." >&2; exit 1; }
[ "$(git rev-parse --abbrev-ref HEAD)" = main ] || { echo "Releases nur von main." >&2; exit 1; }

sed -i.bak "s/^version = \".*\"/version = \"$version\"/" pyproject.toml && rm pyproject.toml.bak
(cd web && npm version "$version" --no-git-tag-version --allow-same-version >/dev/null)
# the Home Assistant integration has the same version: HACS installs it from the same release (#76)
sed -i.bak "s/\"version\": \".*\"/\"version\": \"$version\"/" custom_components/openampere/manifest.json \
  && rm custom_components/openampere/manifest.json.bak
# the changelog of app, website and GitHub release: the commits since the last tag (#155)
python3 scripts/changelog.py add "$version"
git add pyproject.toml web/package.json web/package-lock.json custom_components/openampere/manifest.json \
  web/public/changelog.json
git commit -q -m "chore(release): $version"
git tag -s "v$version" -m "OpenAmpere $version"
echo "Version $version committet und getaggt. Veröffentlichen mit:"
echo "  git push origin main v$version"

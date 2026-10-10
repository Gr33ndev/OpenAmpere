#!/bin/sh
# Prepares a release: sets the version, adds it to the changelog, commits both and creates a signed tag.
#   scripts/release.sh 0.2.0
# Then push both: git push origin main v0.2.0
# The "Release" workflow builds the Docker image and creates the GitHub release with notes.
# An optional summary for owners goes into changelog/0.2.0.json before ({"de": "…", "en": "…"}, #176). It need not
# be committed: it becomes part of the release commit.
set -eu
cd "$(dirname "$0")/.."
version=${1:?Version angeben, z. B. scripts/release.sh 0.2.0}
case "$version" in v*) version=${version#v} ;; esac
summary="changelog/$version.json"
[ -z "$(git status --porcelain --untracked-files=all -- . ":(exclude)$summary")" ] \
  || { echo "Erst alle Änderungen committen (außer $summary)." >&2; exit 1; }
[ "$(git rev-parse --abbrev-ref HEAD)" = main ] || { echo "Releases nur von main." >&2; exit 1; }
# checked before anything is changed, so a failed release leaves no release commit behind (#222)
! git rev-parse -q --verify "refs/tags/v$version" >/dev/null || { echo "Den Tag v$version gibt es schon." >&2; exit 1; }
git fetch -q origin main
[ "$(git rev-parse HEAD)" = "$(git rev-parse origin/main)" ] \
  || { echo "main ist nicht auf dem Stand von origin/main. Erst git pull (oder pushen)." >&2; exit 1; }

# the changelog of app, website and GitHub release: the commits since the last tag (#155) and the summary (#176).
# First, so a broken summary file stops the release before anything is changed.
python3 scripts/changelog.py add "$version"
sed -i.bak "s/^version = \".*\"/version = \"$version\"/" pyproject.toml && rm pyproject.toml.bak
(cd web && npm version "$version" --no-git-tag-version --allow-same-version >/dev/null)
# the Home Assistant integration has the same version: HACS installs it from the same release (#76)
sed -i.bak "s/\"version\": \".*\"/\"version\": \"$version\"/" custom_components/openampere/manifest.json \
  && rm custom_components/openampere/manifest.json.bak
git add pyproject.toml web/package.json web/package-lock.json custom_components/openampere/manifest.json \
  web/public/changelog.json
[ ! -f "$summary" ] || git add "$summary"
git commit -q -m "chore(release): $version"
git tag -s "v$version" -m "OpenAmpere $version" || {
  echo "Der Release-Commit ist da, der Tag fehlt. Nach dem Beheben (z. B. Signaturschlüssel) nur den Tag nachholen:" >&2
  echo "  git tag -s v$version -m \"OpenAmpere $version\"" >&2
  exit 1
}
echo "Version $version committet und getaggt. Veröffentlichen mit:"
echo "  git push origin main v$version"

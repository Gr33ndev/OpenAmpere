#!/bin/sh
# SPDX-FileCopyrightText: Copyright the OpenAmpere contributors
# SPDX-License-Identifier: MIT
# OpenAmpere updater. Runs in its own small container (docker:cli) with access to Docker, set up by install.sh.
#
# It reacts to one thing only: the file data/update/request, which the app writes when someone taps
# "Aktualisieren" (or at night with automatic updates). Then it pulls the new images of this installation,
# restarts the containers and checks that OpenAmpere keeps running and answers (health check of the image). If not, the
# previous version comes back.
# The app itself never gets access to Docker, it can only ask for an update.
#
# Before a new OpenAmpere image is started, its build provenance is verified with cosign (#115): the image must have
# been built by the release workflow of github.com/Gr33ndev/OpenAmpere (signed attestation, Sigstore transparency log).
# No account is needed. If the check fails, the new image is not started and the previous version keeps running.
# OPENAMPERE_VERIFY_IMAGES=nein turns the check off (only for emergencies).
set -u

DIR=${OPENAMPERE_DIR:-/opt/openampere}
STATE="$DIR/data/update"
APP=openampere
CHECK_AFTER_S=${OPENAMPERE_CHECK_AFTER_S:-60} # how long the new version must run without crashing
HEALTH_WAIT_S=${OPENAMPERE_HEALTH_WAIT_S:-240} # extra wait while the health check is starting (start period 180 s in the Dockerfile)
VERIFY=${OPENAMPERE_VERIFY_IMAGES:-ja}
OWN_IMAGE=ghcr.io/gr33ndev/openampere
SIGNER='^https://github.com/Gr33ndev/OpenAmpere/\.github/workflows/release\.yml@refs/tags/v'

mkdir -p "$STATE"
chown 1000:1000 "$STATE" 2>/dev/null || true # the app (user 1000) writes the request here
cd "$DIR" || exit 1

status() { # state message
  printf '{"ts": %s, "state": "%s", "message": "%s"}\n' "$(date +%s)" "$1" "$2" >"$STATE/status.json.tmp"
  mv "$STATE/status.json.tmp" "$STATE/status.json"
}

container() { docker compose ps -q "$APP" 2>/dev/null | head -n 1; }

# exit code 0 if the new version runs: not restarted by Docker (a version that crashes is restarted again and again,
# then its restart count grows) and healthy, if the image has a health check (#167; older images have none).
# While the health check is still starting (slow first start, e.g. on a Raspberry Pi), wait a bit longer.
started() {
  waited=0
  while :; do
    # shellcheck disable=SC2046 # three words: running, restart count, health status (empty without a health check)
    set -- $(docker inspect -f '{{.State.Running}} {{.RestartCount}} {{if .State.Health}}{{.State.Health.Status}}{{end}}' "$(container)" 2>/dev/null)
    if [ "${1:-}" != true ] || [ "${2:-}" != 0 ]; then return 1; fi
    case "${3:-}" in
      "" | healthy) return 0 ;;
      starting) [ "$waited" -lt "$HEALTH_WAIT_S" ] || return 1 ;;
      *) return 1 ;; # unhealthy: e.g. the server hangs
    esac
    date +%s >"$STATE/updater-alive"
    sleep 10
    waited=$((waited + 10))
  done
}

# verified image: exit code 0 if the image may be started. Only OpenAmpere's own image is checked.
verified() {
  case "$VERIFY" in [nN]*) return 0 ;; esac
  case "$1" in "$OWN_IMAGE" | "$OWN_IMAGE":*) ;; *) return 0 ;; esac
  command -v cosign >/dev/null || apk add --no-cache -q cosign >/dev/null 2>&1 || return 1
  ref=$(docker image inspect -f '{{range .RepoDigests}}{{println .}}{{end}}' "$1" 2>/dev/null | grep "^$OWN_IMAGE@" | head -n 1)
  [ -n "$ref" ] || return 1
  cosign verify-attestation --new-bundle-format --type slsaprovenance1 \
    --certificate-oidc-issuer https://token.actions.githubusercontent.com --certificate-identity-regexp "$SIGNER" \
    "$ref" >/dev/null 2>&1
}

update() {
  services=$(docker compose config --services | grep -vx updater | tr '\n' ' ')
  # OpenAmpere's image from the compose file, not from the running container: when the app is not running (crashed,
  # stopped), the check must not be skipped. Another image (built by hand) is not checked, as before.
  image=$(docker compose config --images 2>/dev/null | grep -m 1 -E "^$OWN_IMAGE(:|@|\$)")
  [ -n "$image" ] || image=$(docker inspect -f '{{.Config.Image}}' "$(container)" 2>/dev/null)
  if [ -z "$image" ]; then
    status failed "Die Einstellungen der Installation ließen sich nicht lesen. Bitte das Installationsscript noch einmal ausführen."
    return
  fi
  old=$(docker inspect -f '{{.Image}}' "$(container)" 2>/dev/null) # the version that runs now, empty if none runs
  before=$(docker image inspect -f '{{.Id}}' "$image" 2>/dev/null)
  status pulling "Die neue Version wird heruntergeladen."
  # shellcheck disable=SC2086 # one word per service
  if ! docker compose pull --quiet $services; then
    status failed "Herunterladen hat nicht geklappt. Bitte die Internetverbindung prüfen."
    return
  fi
  new=$(docker image inspect -f '{{.Id}}' "$image" 2>/dev/null)
  if [ -z "$new" ]; then
    status failed "Herunterladen hat nicht geklappt. Bitte die Internetverbindung prüfen."
    return
  fi
  # every image that is not the one running now is checked before it is started, also when none runs
  if [ "$new" != "$old" ]; then
    status verifying "Die Herkunft der neuen Version wird geprüft."
    if ! verified "$image"; then
      # back to the version before, so the unverified image is not started later either (e.g. after a reboot)
      keep=${old:-$before}
      if [ -n "$keep" ] && [ "$keep" != "$new" ]; then
        docker tag "$keep" "$image" && docker image rm "$new" >/dev/null 2>&1
      else
        docker image rm "$image" >/dev/null 2>&1
      fi
      status failed "Die neue Version konnte nicht als echt bestätigt werden und wird nicht installiert. Die bisherige Version läuft weiter."
      return
    fi
  fi
  status restarting "Die neue Version wird gestartet."
  # shellcheck disable=SC2086
  docker compose up -d $services
  if [ -n "$old" ] && [ "$new" = "$old" ]; then
    status "done" "OpenAmpere ist schon auf dem neuesten Stand."
    return
  fi
  sleep "$CHECK_AFTER_S"
  if started; then
    status "done" "Update installiert."
    docker image prune -f >/dev/null 2>&1
  elif [ -n "$old" ]; then
    docker tag "$old" "$image" && docker compose up -d "$APP"
    status failed "Die neue Version ist nicht richtig gestartet. Die bisherige Version läuft wieder."
  else
    status failed "Die neue Version ist nicht richtig gestartet."
  fi
}

main() {
  # "updater.sh verify IMAGE": only the provenance check, for install.sh before it starts a new image
  if [ "${1:-}" = verify ]; then
    verified "${2:-}"
    exit $?
  fi
  # cosign for the provenance check, installed once when the helper starts (again later if this fails, e.g. offline)
  case "$VERIFY" in [nN]*) ;; *) apk add --no-cache -q cosign >/dev/null 2>&1 || true ;; esac

  # stop at once when Docker stops the container (sh as the first process ignores TERM otherwise and waits 10 s)
  trap 'exit 0' TERM INT
  while true; do
    date +%s >"$STATE/updater-alive"
    if [ -f "$STATE/request" ]; then
      rm -f "$STATE/request"
      update
    fi
    sleep 5 &
    wait $!
  done
}

main "$@"

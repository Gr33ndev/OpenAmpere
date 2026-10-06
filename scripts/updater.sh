#!/bin/sh
# OpenAmpere updater. Runs in its own small container (docker:cli) with access to Docker, set up by install.sh.
#
# It reacts to one thing only: the file data/update/request, which the app writes when someone taps
# "Aktualisieren" (or at night with automatic updates). Then it pulls the new images of this installation,
# restarts the containers and checks that OpenAmpere keeps running. If not, the previous version comes back.
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
  current=$(container)
  image=$(docker inspect -f '{{.Config.Image}}' "$current" 2>/dev/null)
  old=$(docker inspect -f '{{.Image}}' "$current" 2>/dev/null)
  status pulling "Die neue Version wird heruntergeladen."
  # shellcheck disable=SC2086 # one word per service
  if ! docker compose pull --quiet $services; then
    status failed "Herunterladen hat nicht geklappt. Bitte die Internetverbindung prüfen."
    return
  fi
  new=$(docker image inspect -f '{{.Id}}' "$image" 2>/dev/null)
  if [ -n "$new" ] && [ "$new" != "$old" ]; then
    status verifying "Die Herkunft der neuen Version wird geprüft."
    if ! verified "$image"; then
      # keep the tag on the running version, so the unverified image is not started later either (e.g. after a reboot)
      [ -n "$old" ] && docker tag "$old" "$image" && docker image rm "$new" >/dev/null 2>&1
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
  # a version that crashes is restarted again and again by Docker: then its restart count grows
  if [ "$(docker inspect -f '{{.State.Running}} {{.RestartCount}}' "$(container)" 2>/dev/null)" = "true 0" ]; then
    status "done" "Update installiert."
    docker image prune -f >/dev/null 2>&1
  elif [ -n "$old" ]; then
    docker tag "$old" "$image" && docker compose up -d "$APP"
    status failed "Die neue Version ist nicht richtig gestartet. Die bisherige Version läuft wieder."
  else
    status failed "Die neue Version ist nicht richtig gestartet."
  fi
}

# cosign for the provenance check, installed once when the helper starts (again later if this fails, e.g. offline)
case "$VERIFY" in [nN]*) ;; *) apk add --no-cache -q cosign >/dev/null 2>&1 || true ;; esac

while true; do
  date +%s >"$STATE/updater-alive"
  if [ -f "$STATE/request" ]; then
    rm -f "$STATE/request"
    update
  fi
  sleep 5
done

#!/usr/bin/env bash
# SPDX-FileCopyrightText: Copyright the OpenAmpere contributors
# SPDX-License-Identifier: MIT
# Installs or updates OpenAmpere with Docker on a Linux machine (Raspberry Pi OS, Debian, Ubuntu, Proxmox ...).
#
#   curl -fsSL https://gr33ndev.github.io/OpenAmpere/install.sh | bash
#
# Asks at most four questions (install Docker? install folder? wallbox with evcc? access from anywhere with
# Tailscale?) and detects the rest. Running it again updates an existing installation and keeps the answers.
# Without questions, e.g. for automation:
#   OPENAMPERE_YES=1 OPENAMPERE_DIR=/opt/openampere OPENAMPERE_EVCC=nein OPENAMPERE_TAILSCALE=nein bash install.sh
set -euo pipefail

IMAGE="ghcr.io/gr33ndev/openampere:latest"
EVCC_IMAGE="evcc/evcc:latest"
TAILSCALE_IMAGE="tailscale/tailscale:stable"
UPDATER_IMAGE="docker:cli"
SITE="https://gr33ndev.github.io/OpenAmpere"
APP_UID=1000  # user inside the OpenAmpere image
MARKER="# erzeugt von install.sh"
NO_TAILSCALE="# Zugriff von unterwegs (Tailscale): nein"  # remembers the answer, so the question comes only once
DOCS="https://github.com/Gr33ndev/OpenAmpere/blob/main/README.de.md#installation-von-hand"
HELP="https://github.com/Gr33ndev/OpenAmpere/discussions/categories/q-a"  # questions and answers

if [ -t 1 ]; then BOLD=$'\e[1m' GREEN=$'\e[32m' RED=$'\e[31m' RESET=$'\e[0m'; else BOLD="" GREEN="" RED="" RESET=""; fi
say() { printf '%s\n' "$*"; }
step() { printf '\n%s==> %s%s\n' "$BOLD" "$*" "$RESET"; }
fail() { printf '\n%sFehler:%s %s\n' "$RED" "$RESET" "$*" >&2; exit 1; }

interactive() { [ "${OPENAMPERE_YES:-}" != 1 ] && { : </dev/tty; } 2>/dev/null; }

# ask "Frage" "Vorgabe" -> answer on stdout (reads from the terminal, also when the script comes through a pipe)
ask() {
  local answer=""
  if interactive; then
    printf '%s [%s]: ' "$1" "$2" >/dev/tty
    read -r answer </dev/tty || true
  fi
  printf '%s' "${answer:-$2}"
}

# confirm "Frage" j|n -> exit code 0 for yes
confirm() {
  local hint answer
  [ "$2" = j ] && hint="J/n" || hint="j/N"
  answer=$(ask "$1" "$hint")
  [ "$answer" = "$hint" ] && answer=$2
  case "$answer" in [jJyY]*) return 0 ;; *) return 1 ;; esac
}

# install_dir -> the folder: OPENAMPERE_DIR, an earlier installation (also in another folder) or the answer.
# Always an absolute path: compose needs one for the helpers, and "~" is not expanded in an answer (#222).
install_dir() {
  local dir=${OPENAMPERE_DIR:-/opt/openampere} earlier
  if [ -z "${OPENAMPERE_DIR:-}" ] && [ ! -f "$dir/docker-compose.yml" ]; then
    earlier=$($DOCKER ps -a --filter label=com.docker.compose.service=openampere \
      --format '{{index .Labels "com.docker.compose.project.working_dir"}}' 2>/dev/null | head -n 1 || true)
    if [ -n "$earlier" ] && [ -f "$earlier/docker-compose.yml" ]; then
      dir=$earlier
    else
      dir=$(ask "Installationsordner" "$dir")
    fi
  fi
  # shellcheck disable=SC2088 # compares with a literal "~" that was typed in, on purpose
  case "$dir" in "~") dir=$HOME ;; "~/"*) dir="$HOME/${dir#\~/}" ;; esac
  case "$dir" in
    /*) printf '%s' "${dir%/}" ;;
    *) fail "Bitte den Installationsordner als vollständigen Pfad angeben, zum Beispiel /opt/openampere." ;;
  esac
}

port_in_use() { (exec 3<>"/dev/tcp/localhost/$1") 2>/dev/null; }

# access from anywhere: OPENAMPERE_TAILSCALE or the question -> TAILSCALE=yes|no
choose_tailscale() {
  if [ -n "${OPENAMPERE_TAILSCALE:-}" ]; then
    case "$OPENAMPERE_TAILSCALE" in [jJyY]*) TAILSCALE=yes ;; *) TAILSCALE=no ;; esac
  elif confirm "Möchtest du OpenAmpere auch von unterwegs nutzen? Dafür richte ich Tailscale (tailscale.com) mit ein, angemeldet wird später in der App." n; then
    TAILSCALE=yes
  else
    TAILSCALE=no
  fi
}

main() {
  say "${BOLD}OpenAmpere installieren${RESET}"
  say "Lokale App für Solaranlagen mit Speicher. Mehr dazu: https://gr33ndev.github.io/OpenAmpere/"

  # --- system ---
  [ "$(uname -s)" = Linux ] || fail "Dieses Script ist für Linux. Für Docker Desktop siehe $DOCS"
  case "$(uname -m)" in
    x86_64 | amd64 | aarch64 | arm64) ;;
    armv7l | armv6l) fail "32-Bit-System erkannt. Bitte ein 64-Bit-System verwenden, z. B. Raspberry Pi OS (64-bit)." ;;
    *) fail "Prozessor $(uname -m) wird nicht unterstützt." ;;
  esac
  SUDO=""
  if [ "$(id -u)" != 0 ]; then
    command -v sudo >/dev/null || fail "Bitte als root ausführen oder sudo installieren."
    SUDO="sudo"
  fi

  # --- Docker ---
  step "Docker prüfen"
  if ! command -v docker >/dev/null; then
    confirm "Docker ist nicht installiert. Jetzt über get.docker.com installieren?" j || fail "Ohne Docker geht es nicht."
    curl -fsSL https://get.docker.com | $SUDO sh
  fi
  DOCKER="docker"
  if ! docker info >/dev/null 2>&1; then
    DOCKER="$SUDO docker"
    $DOCKER info >/dev/null 2>&1 || $SUDO systemctl enable --now docker >/dev/null 2>&1 || true
    $DOCKER info >/dev/null 2>&1 || fail "Docker läuft nicht. Prüfen mit: sudo systemctl status docker"
  fi
  $DOCKER compose version >/dev/null 2>&1 || fail "Docker Compose fehlt. Installieren mit: sudo apt install docker-compose-plugin"
  say "Docker ist bereit."

  # --- folder, update of an existing installation ---
  DIR=$(install_dir)
  COMPOSE="$DIR/docker-compose.yml"
  if [ -f "$COMPOSE" ]; then
    grep -q "$MARKER" "$COMPOSE" ||
      fail "$DIR wurde von Hand eingerichtet. Aktualisieren dort mit: git pull && docker compose up -d --build"
    step "OpenAmpere ist schon installiert in $DIR"
    confirm "Auf die neueste Version aktualisieren?" j || exit 0
    # keep the choices of the first installation, but bring the files up to date (e.g. the update helper)
    PORT=$(sed -n 's/.*OPENAMPERE_SERVER_PORT: "\([0-9]*\)".*/\1/p' "$COMPOSE")
    PORT=${PORT:-8080}
    TLS_PORT=$(sed -n 's/.*OPENAMPERE_SERVER_TLS_PORT: "\([0-9]*\)".*/\1/p' "$COMPOSE")
    TLS_PORT=${TLS_PORT:-8443}
    TZ_NAME=$(sed -n 's/^ *TZ: *//p' "$COMPOSE" | head -n 1)
    TZ_NAME=${TZ_NAME:-Europe/Berlin}
    EVCC_CONTAINER=no
    grep -q "^  evcc:" "$COMPOSE" && EVCC_CONTAINER=yes
    if [ -n "${OPENAMPERE_TAILSCALE:-}" ]; then
      choose_tailscale  # changing the answer on purpose
    elif grep -q "^  tailscale:" "$COMPOSE"; then
      TAILSCALE=yes
    elif grep -qF "$NO_TAILSCALE" "$COMPOSE"; then
      TAILSCALE=no
    else
      choose_tailscale  # installed before this question existed
    fi
    write_files
    start_and_report
    return
  fi

  # --- wallbox ---
  EVCC_CONTAINER=no EVCC_URL=""
  if [ -n "${OPENAMPERE_EVCC:-}" ]; then
    case "$OPENAMPERE_EVCC" in [jJyY]*) EVCC=yes ;; *) EVCC=no ;; esac
  elif confirm "Hast du eine Wallbox? Sie wird über evcc (evcc.io) gesteuert, das ich dann mit einrichte." n; then
    EVCC=yes
  else
    EVCC=no
  fi
  if [ "$EVCC" = yes ]; then
    EVCC_URL="http://localhost:7070"
    if port_in_use 7070; then
      say "Auf Port 7070 läuft schon etwas, vermutlich evcc. OpenAmpere verbindet sich mit diesem evcc."
    else
      EVCC_CONTAINER=yes
    fi
  fi

  # --- access from anywhere ---
  choose_tailscale

  # --- detected settings ---
  PORT=8080
  while port_in_use "$PORT"; do
    PORT=$((PORT + 1))
    [ "$PORT" -le 8099 ] || fail "Kein freier Port zwischen 8080 und 8099 gefunden."
  done
  # HTTPS for other apps such as Home Assistant
  TLS_PORT=8443
  while port_in_use "$TLS_PORT"; do
    TLS_PORT=$((TLS_PORT + 1))
    [ "$TLS_PORT" -le 8459 ] || fail "Kein freier Port zwischen 8443 und 8459 gefunden."
  done
  TZ_NAME=$(timedatectl show -p Timezone --value 2>/dev/null || cat /etc/timezone 2>/dev/null || true)
  [ -n "$TZ_NAME" ] || TZ_NAME="Europe/Berlin"

  write_files
  if [ ! -f "$DIR/data/config.yaml" ]; then
    {
      say "# Startwerte, alles lässt sich danach in der App ändern."
      say "timezone: $TZ_NAME"
      if [ -n "$EVCC_URL" ]; then
        say "evcc:"
        say "  url: $EVCC_URL"
      fi
    } | $SUDO tee "$DIR/data/config.yaml" >/dev/null
    $SUDO chown "$APP_UID:$APP_UID" "$DIR/data/config.yaml"
  fi

  start_and_report
}

# copy_helper name: a helper script next to this script when run from the repository, otherwise from the website
copy_helper() {
  local source=${BASH_SOURCE[0]:-}
  if [ -n "$source" ] && [ -f "$source" ] && [ -f "$(dirname "$source")/$1" ]; then
    $SUDO cp "$(dirname "$source")/$1" "$DIR/$1"
  else
    # first into a new file: a failed or cut download must not leave an empty helper behind (#222)
    if ! curl -fsSL "$SITE/$1" | $SUDO tee "$DIR/$1.new" >/dev/null || [ ! -s "$DIR/$1.new" ]; then
      $SUDO rm -f "$DIR/$1.new"
      fail "$1 ließ sich nicht herunterladen. Bitte die Internetverbindung prüfen und das Script noch einmal ausführen."
    fi
    $SUDO mv "$DIR/$1.new" "$DIR/$1"
  fi
  $SUDO chmod 755 "$DIR/$1"
}

# Docker keeps the messages of a container without limit by default, which fills SD cards over time
log_limit() {
  say "    logging:  # höchstens 3 × 10 MB Meldungen, damit der Speicher nicht vollläuft"
  say "      driver: json-file"
  say "      options:"
  say "        max-size: \"10m\""
  say "        max-file: \"3\""
}

# docker-compose.yml and the helpers; uses DIR, COMPOSE, PORT, TLS_PORT, TZ_NAME, EVCC_CONTAINER, TAILSCALE
write_files() {
  step "Dateien schreiben in $DIR"
  $SUDO mkdir -p "$DIR/data"
  $SUDO chown "$APP_UID:$APP_UID" "$DIR/data"
  {
    say "$MARKER am $(date +%F). Aktualisieren: in der App oder das Script erneut ausführen."
    [ "$TAILSCALE" = yes ] || say "$NO_TAILSCALE. Ändern: Script erneut mit OPENAMPERE_TAILSCALE=ja ausführen."
    say "services:"
    say "  openampere:"
    say "    image: $IMAGE"
    say "    restart: unless-stopped"
    say "    network_mode: host  # damit die Gerätesuche das Heimnetz sieht"
    say "    volumes:"
    say "      - ./data:/data"
    say "    environment:"
    say "      TZ: $TZ_NAME"
    [ "$PORT" = 8080 ] || say "      OPENAMPERE_SERVER_PORT: \"$PORT\""
    [ "${TLS_PORT:-8443}" = 8443 ] || say "      OPENAMPERE_SERVER_TLS_PORT: \"$TLS_PORT\""
    log_limit
    if [ "$EVCC_CONTAINER" = yes ]; then
      say "  evcc:  # steuert die Wallbox, https://evcc.io"
      say "    image: $EVCC_IMAGE"
      say "    restart: unless-stopped"
      say "    network_mode: host"
      say "    volumes:"
      say "      - ./evcc:/root/.evcc"
      log_limit
    fi
    if [ "$TAILSCALE" = yes ]; then
      say "  tailscale:  # Zugriff von unterwegs, eingerichtet wird in der App (siehe tailscale.sh)"
      say "    image: $TAILSCALE_IMAGE"
      say "    restart: unless-stopped"
      say "    network_mode: host  # damit Tailscale die App auf diesem Rechner erreicht"
      say "    entrypoint: [\"sh\", \"/openampere/tailscale.sh\"]"
      say "    volumes:"
      say "      - ./tailscale:/var/lib/tailscale"
      say "      - ./data/remote:/remote"
      say "      - ./tailscale.sh:/openampere/tailscale.sh:ro"
      say "    environment:"
      say "      OPENAMPERE_PORT: \"$PORT\"  # für HTTPS über Tailscale"
      log_limit
    fi
    say "  updater:  # installiert Updates, wenn in der App jemand auf Aktualisieren tippt (siehe updater.sh)"
    say "    image: $UPDATER_IMAGE"
    say "    restart: unless-stopped"
    say "    entrypoint: [\"sh\", \"$DIR/updater.sh\"]"
    say "    environment:"
    say "      OPENAMPERE_DIR: $DIR"
    say "    volumes:"
    say "      - /var/run/docker.sock:/var/run/docker.sock"
    say "      - $DIR:$DIR"
    log_limit
  } | $SUDO tee "$COMPOSE" >/dev/null
  copy_helper updater.sh
  [ "$TAILSCALE" = yes ] && copy_helper tailscale.sh
  say "Fertig: $COMPOSE"
}

# the provenance check of the update helper (cosign, signed by the release workflow), in a short-lived helper container
verify_image() {
  $DOCKER compose run --rm --no-deps -T -e OPENAMPERE_VERIFY_IMAGES="${OPENAMPERE_VERIFY_IMAGES:-ja}" \
    --entrypoint sh updater "$DIR/updater.sh" verify "$1"
}

start_and_report() {
  step "OpenAmpere herunterladen und starten"
  cd "$DIR"
  local image running before new keep
  image=$IMAGE
  running=$($DOCKER inspect -f '{{.Image}}' "$($DOCKER compose ps -q openampere 2>/dev/null | head -n 1)" 2>/dev/null) || running=""
  before=$($DOCKER image inspect -f '{{.Id}}' "$image" 2>/dev/null) || before=""
  $DOCKER compose pull ||
    fail "OpenAmpere ließ sich nicht herunterladen. Bitte die Internetverbindung dieses Rechners prüfen und das Script noch einmal ausführen. Klappt es dann immer noch nicht, frag hier nach (die Meldungen oben helfen dabei): $HELP"
  new=$($DOCKER image inspect -f '{{.Id}}' "$image" 2>/dev/null) || new=""
  # like the update helper: an image that does not run yet is only started if its provenance is confirmed
  if [ -z "$new" ] || { [ "$new" != "$running" ] && ! verify_image "$image"; }; then
    keep=${running:-$before}
    if [ -n "$keep" ] && [ "$keep" != "$new" ]; then
      $DOCKER tag "$keep" "$image" && $DOCKER image rm "$new" >/dev/null 2>&1
    elif [ -n "$new" ]; then
      $DOCKER image rm "$image" >/dev/null 2>&1
    fi
    fail "Die heruntergeladene Version von OpenAmpere konnte nicht als echt bestätigt werden und wird nicht gestartet. Bitte später noch einmal versuchen. Klappt es dann immer noch nicht, frag hier nach: $HELP"
  fi
  $DOCKER compose up -d --remove-orphans
  # the helpers read their script once at the start: restart them so a new updater.sh / tailscale.sh is used (#222)
  local helpers
  helpers=$($DOCKER compose config --services | grep -xE 'updater|tailscale' | tr '\n' ' ')
  # shellcheck disable=SC2086 # one word per service
  [ -z "$helpers" ] || $DOCKER compose up -d --no-deps --force-recreate $helpers

  local port ip
  port=$(sed -n 's/.*OPENAMPERE_SERVER_PORT: "\([0-9]*\)".*/\1/p' docker-compose.yml)
  port=${port:-8080}
  ip=$(hostname -I 2>/dev/null | awk '{print $1}')
  ip=${ip:-<server-ip>}

  printf 'Warte auf den Start '
  local ok=no
  for _ in $(seq 1 30); do
    if (exec 3<>"/dev/tcp/localhost/$port") 2>/dev/null; then ok=yes; break; fi
    printf '.'
    sleep 2
  done
  say ""
  if [ "$ok" != yes ]; then
    $DOCKER compose logs --tail 30 openampere || true
    fail "OpenAmpere ist nicht gestartet. Die letzten Meldungen stehen oben. Bitte das Script noch einmal ausführen. Klappt es dann immer noch nicht, frag hier nach (mit diesen Meldungen): $HELP"
  fi

  say ""
  say "${GREEN}${BOLD}OpenAmpere läuft.${RESET}"
  say ""
  say "  Im Browser öffnen:  ${BOLD}http://$ip:$port${RESET}"
  say "  Dort ein Passwort festlegen. Der Assistent sucht dann den Wechselrichter."
  say "  Am Wechselrichter muss Modbus TCP eingeschaltet sein."
  local tls_port
  tls_port=$(sed -n 's/.*OPENAMPERE_SERVER_TLS_PORT: "\([0-9]*\)".*/\1/p' docker-compose.yml)
  say ""
  say "  Home Assistant: Integration OpenAmpere über HACS installieren, dann koppeln mit"
  say "  Adresse ${BOLD}$ip${RESET} und HTTPS-Port ${BOLD}${tls_port:-8443}${RESET}."
  if grep -q "^  evcc:" docker-compose.yml; then
    say ""
    say "  evcc für die Wallbox:  ${BOLD}http://$ip:7070${RESET}"
    say "  Dort Wallbox und Fahrzeug einrichten. Die Zähler von OpenAmpere übernimmst du"
    say "  aus der App unter Mehr → Verbindung → Wallbox."
  fi
  if grep -q "^  tailscale:" docker-compose.yml; then
    say ""
    say "  Von unterwegs: in der App unter Mehr → Zugriff von unterwegs auf Einrichten tippen."
  fi
  say ""
  say "  Neue Versionen zeigt die App oben an, ein Tipp auf Aktualisieren genügt."
  say "  Automatisch nachts: in der App unter Mehr → Über OpenAmpere."
  say "  Meldungen ansehen: cd $DIR && $DOCKER compose logs -f"
}

main "$@"

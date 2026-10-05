#!/bin/sh
# OpenAmpere: access from anywhere with Tailscale. Runs in the tailscale container (image tailscale/tailscale)
# that install.sh sets up when someone wants to use OpenAmpere away from home.
#
# Like the updater it reacts to one thing only: the file data/remote/request, which the app writes when someone
# taps "Einrichten" (login) or "Trennen" (logout). It writes the state of Tailscale back next to it
# (status.json, the output of `tailscale status --json`). The app itself never gets access to Tailscale.
# Until someone logs in, Tailscale stays logged out.
set -u

STATE=${OPENAMPERE_REMOTE_DIR:-/remote}
SOCKET=/tmp/tailscaled.sock
NAME=${OPENAMPERE_REMOTE_NAME:-openampere} # name of this machine in the tailnet: openampere.<tailnet>.ts.net

mkdir -p "$STATE"
chown 1000:1000 "$STATE" 2>/dev/null || true # the app (user 1000) writes the request here

# userspace networking: no extra privileges and no /dev/net/tun (NAS, Proxmox LXC); connections to the
# tailnet address of this machine end up on its own ports, e.g. OpenAmpere on 8080
tailscaled --tun=userspace-networking --statedir=/var/lib/tailscale --socket="$SOCKET" --no-logs-no-support &

ts() { tailscale --socket="$SOCKET" "$@"; }

publish() {
  if ts status --json --peers=false >"$STATE/status.json.tmp" 2>/dev/null; then
    mv "$STATE/status.json.tmp" "$STATE/status.json"
  else
    rm -f "$STATE/status.json.tmp"
  fi
}

login_pid=""
while true; do
  date +%s >"$STATE/alive"
  if [ -f "$STATE/request" ]; then
    request=$(cat "$STATE/request" 2>/dev/null)
    rm -f "$STATE/request"
    [ -n "$login_pid" ] && kill "$login_pid" 2>/dev/null
    login_pid=""
    case "$request" in
      login)
        # waits until someone has logged in with the link; the link also shows up in status.json (AuthURL).
        # --accept-dns=false: Tailscale never touches the DNS settings of this machine
        ts login --hostname="$NAME" --accept-dns=false >"$STATE/login.log" 2>&1 &
        login_pid=$!
        ;;
      logout)
        ts logout >"$STATE/login.log" 2>&1
        ;;
    esac
  fi
  publish
  sleep 3
done

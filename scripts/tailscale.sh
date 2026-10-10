#!/bin/sh
# OpenAmpere: access from anywhere with Tailscale. Runs in the tailscale container (image tailscale/tailscale)
# that install.sh sets up when someone wants to use OpenAmpere away from home.
#
# Like the updater it reacts to one thing only: the file data/remote/request, which the app writes when someone
# taps "Einrichten" (login), "Trennen" (logout) or switches HTTPS on or off (https-on, https-off: `tailscale serve`
# with a certificate for openampere.<tailnet>.ts.net, #116). It writes the state of Tailscale back next to it
# (status.json and serve.json, the output of `tailscale status --json` and `tailscale serve status --json`, plus the
# output of the last serve command in serve.log). The app itself never gets access to Tailscale.
# Until someone logs in, Tailscale stays logged out.
set -u

STATE=${OPENAMPERE_REMOTE_DIR:-/remote}
SOCKET=/tmp/tailscaled.sock
NAME=${OPENAMPERE_REMOTE_NAME:-openampere} # name of this machine in the tailnet: openampere.<tailnet>.ts.net
PORT=${OPENAMPERE_PORT:-8080} # where OpenAmpere listens on this machine, for HTTPS via tailscale serve

mkdir -p "$STATE"
chown 1000:1000 "$STATE" 2>/dev/null || true # the app (user 1000) writes the request here

# userspace networking: no extra privileges and no /dev/net/tun (NAS, Proxmox LXC); connections to the
# tailnet address of this machine end up on its own ports, e.g. OpenAmpere on 8080
tailscaled --tun=userspace-networking --statedir=/var/lib/tailscale --socket="$SOCKET" --no-logs-no-support &
daemon=$!
# stop at once when Docker stops the container (sh as the first process ignores TERM otherwise and waits 10 s)
trap 'kill "$daemon" 2>/dev/null; exit 0' TERM INT

ts() { tailscale --socket="$SOCKET" "$@"; }

publish() {
  if ts status --json --peers=false >"$STATE/status.json.tmp" 2>/dev/null; then
    mv "$STATE/status.json.tmp" "$STATE/status.json"
  else
    rm -f "$STATE/status.json.tmp"
  fi
  if ts serve status --json >"$STATE/serve.json.tmp" 2>/dev/null; then
    mv "$STATE/serve.json.tmp" "$STATE/serve.json"
  else
    rm -f "$STATE/serve.json.tmp"
  fi
}

login_pid=""
serve_pid=""
while true; do
  # tailscaled stopped: end the container, Docker starts it again (restart: unless-stopped), #222
  kill -0 "$daemon" 2>/dev/null || { echo "tailscaled ist beendet" >&2; exit 1; }
  date +%s >"$STATE/alive"
  if [ -f "$STATE/request" ]; then
    request=$(cat "$STATE/request" 2>/dev/null)
    rm -f "$STATE/request"
    case "$request" in
      login | logout)
        [ -n "$login_pid" ] && kill "$login_pid" 2>/dev/null
        login_pid=""
        ;;
    esac
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
      https-on)
        # HTTPS with a certificate for this machine's tailnet name, forwarded to OpenAmpere. If HTTPS (or serve) is
        # not enabled in the tailnet yet, Tailscale prints a link to enable it and waits; the app shows that link.
        [ -n "$serve_pid" ] && kill "$serve_pid" 2>/dev/null
        ts serve --bg --https=443 "http://127.0.0.1:$PORT" >"$STATE/serve.log" 2>&1 &
        serve_pid=$!
        ;;
      https-off)
        [ -n "$serve_pid" ] && kill "$serve_pid" 2>/dev/null
        serve_pid=""
        ts serve --https=443 off >"$STATE/serve.log" 2>&1
        ;;
    esac
  fi
  publish
  sleep 3 &
  wait $!
done

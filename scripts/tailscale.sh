#!/bin/sh
# SPDX-FileCopyrightText: Copyright the OpenAmpere contributors
# SPDX-License-Identifier: MIT
# OpenAmpere: access from anywhere with Tailscale. Runs in the tailscale container (image tailscale/tailscale)
# that install.sh sets up when someone wants to use OpenAmpere away from home.
#
# Like the updater it reacts to one thing only: the file remote/inbox/request, which the app writes when someone
# taps "Einrichten" (login), "Trennen" (logout) or switches HTTPS on or off (https-on, https-off: `tailscale serve`
# with a certificate for openampere.<tailnet>.ts.net, #116). It writes the state of Tailscale back next to it
# (status.json and serve.json, the output of `tailscale status --json` and `tailscale serve status --json`, plus the
# output of the last serve command in serve.log). The app itself never gets access to Tailscale.
# Until someone logs in, Tailscale stays logged out.
set -u

# written only by this helper (root) and read by the app; the app writes into inbox only, so it cannot place links
# where root writes (GHSA-2j3r-6fcx-9m7v). install.sh creates both.
STATE=${OPENAMPERE_REMOTE_DIR:-/remote}
INBOX="$STATE/inbox"
SOCKET=/tmp/tailscaled.sock
NAME=${OPENAMPERE_REMOTE_NAME:-openampere} # name of this machine in the tailnet: openampere.<tailnet>.ts.net
PORT=${OPENAMPERE_PORT:-8080} # where OpenAmpere listens on this machine, for HTTPS via tailscale serve

mkdir -p "$STATE"

# kernel (install.sh chooses it where the machine has /dev/net/tun): an own network interface, so only services that
# listen on all addresses (OpenAmpere) are reachable from the tailnet, not those meant only for this machine.
# userspace: no extra privileges and no /dev/net/tun (NAS, Proxmox LXC), but Tailscale hands every connection to
# 127.0.0.1 of this machine (GHSA-6r67-5vwf-5x8x)
case "${OPENAMPERE_TAILSCALE_MODE:-userspace}" in
  kernel) TUN=oa-tailscale ;; # not tailscale0: the name of a Tailscale running on the machine itself
  *) TUN=userspace-networking ;;
esac
start_daemon() {
  tailscaled --tun="$1" --statedir=/var/lib/tailscale --socket="$SOCKET" --no-logs-no-support &
  daemon=$!
}
daemon=""
# stop at once when Docker stops the container (sh as the first process ignores TERM otherwise and waits 10 s),
# also during the check below
trap 'kill "$daemon" 2>/dev/null; exit 0' TERM INT
start_daemon "$TUN"
# the TUN device can exist but not be usable (rootless containers, SELinux, some LXC): tailscaled then ends at once.
# Better without its own interface than without remote access at all; the app shows which mode runs (status file).
if [ "$TUN" != userspace-networking ]; then
  sleep "${OPENAMPERE_TUN_CHECK_S:-5}"
  if ! kill -0 "$daemon" 2>/dev/null; then
    echo "Tailscale ließ sich nicht mit eigener Netzwerkschnittstelle starten und läuft jetzt im Userspace-Modus" >&2
    TUN=userspace-networking
    start_daemon "$TUN"
  fi
fi
case "$TUN" in userspace-networking) echo userspace ;; *) echo kernel ;; esac >"$STATE/mode"

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
  if [ -f "$INBOX/request" ]; then
    request=$(head -c 20 "$INBOX/request" 2>/dev/null)
    rm -f "$INBOX/request"
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

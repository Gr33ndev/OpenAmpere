#!/bin/sh
# SPDX-FileCopyrightText: Copyright the OpenAmpere contributors
# SPDX-License-Identifier: MIT
# Start of the Home Assistant app (#300). The Supervisor creates /data as root and starts the container as root.
# Only this script runs as root: it hands /data to the app user and runs OpenAmpere as that user, without any
# capabilities, as in the Docker installation. Before that, it installs the Home Assistant integration (#300). The
# backup commands in config.yaml use it as well (ha-app-start ha-backup-copy), so their files belong to the app user
# too; they skip the integration and the hand-over of /data.
set -eu
# the Home Assistant integration of this version, so app users need no HACS; a failure must not stop the app. The
# result goes to OpenAmpere in its environment: as root, nothing is written into /data, which belongs to the app user.
if [ "$#" -eq 0 ]; then
  OPENAMPERE_HA_INTEGRATION=$(openampere ha-integration) \
    || { echo "Die Home-Assistant-Integration ließ sich nicht installieren." >&2; OPENAMPERE_HA_INTEGRATION=""; }
  export OPENAMPERE_HA_INTEGRATION
  echo "Home-Assistant-Integration: $OPENAMPERE_HA_INTEGRATION"
fi
# only on a normal start: the app user owns /data from then on, and for the backup commands a failure here would
# abort the backup of Home Assistant
[ "$#" -gt 0 ] || chown -R openampere:openampere /data
exec setpriv --reuid=openampere --regid=openampere --init-groups --inh-caps=-all --bounding-set=-all \
  --no-new-privs openampere "$@"

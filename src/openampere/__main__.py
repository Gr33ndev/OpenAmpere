# SPDX-FileCopyrightText: Copyright the OpenAmpere contributors
# SPDX-License-Identifier: MIT
"""Entry point: python -m openampere [--config config.yaml]"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import logging
import os
import sys
from pathlib import Path
from typing import TYPE_CHECKING

import uvicorn

from . import logs

# the app itself (FastAPI, drivers, ...) is imported in main() only: the health check runs regularly and
# must stay light, also on a Raspberry Pi
if TYPE_CHECKING:
    from .runtime import Runtime

# needs no login and no inverter (#356); older images ask /api/auth/status?healthcheck=1, which still answers
HEALTH_PATH = "/api/health"


def check_data_dir(config_path: str | None) -> None:
    """Fail with a clear message if the data folder is not writable (typical: a bind mount created by root)."""
    from .config import build_config, read_yaml

    folder = Path(build_config(read_yaml(config_path))[0].storage.path).resolve().parent
    if folder.is_dir() and not os.access(folder, os.W_OK):
        raise SystemExit(f"Kein Schreibzugriff auf {folder} (Benutzer {os.getuid()}). Bei Docker auf dem Server im "
                         f"OpenAmpere-Ordner ausführen: sudo chown -R {os.getuid()}:{os.getgid()} data")


def healthcheck(config_path: str | None, timeout: float = 5.0) -> int:
    """For Docker's HEALTHCHECK: 0 if the web server answers on its configured port, 1 if not."""
    import urllib.request

    from .config import build_config, read_yaml

    server = build_config(read_yaml(config_path))[0].server
    host = server.host if server.host not in ("", "0.0.0.0", "::") else "127.0.0.1"
    if ":" in host:
        host = f"[{host}]"
    url = f"http://{host}:{server.port}{HEALTH_PATH}"
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))  # never via a proxy from the environment
    try:
        with opener.open(url, timeout=timeout) as response:
            response.read()
            return 0 if response.status == 200 else 1
    except (OSError, ValueError) as err:  # includes HTTP errors and timeouts
        print(f"OpenAmpere antwortet nicht unter {url}: {err}")
        return 1


class _HideHealthchecks(logging.Filter):
    """The health check runs every 30 seconds: keep it out of the access log, so the log stays readable."""

    def filter(self, record: logging.LogRecord) -> bool:
        args = record.args
        return not (isinstance(args, tuple) and len(args) > 2 and args[2] == HEALTH_PATH)


class _SecondServer(uvicorn.Server):
    """The HTTPS port: same app, but the HTTP server owns the signals and the app's startup and shutdown."""

    @contextlib.contextmanager
    def capture_signals(self):
        yield


async def serve(app, runtime: Runtime) -> None:
    server = runtime.config.server
    # uvicorn logs through our handlers (level from the settings, kept for the diagnostics report); no access log:
    # a line with the client's address for every request of the browser and of Home Assistant does not help (#169)
    # no proxy headers: Tailscale in userspace mode delivers connections of every device in the tailnet from
    # 127.0.0.1, which uvicorn trusts by default, so any of them could choose its address (login limits) and claim
    # HTTPS with X-Forwarded-For / X-Forwarded-Proto (GHSA-7qj3-8hhh-r724)
    quiet = dict(log_config=None, log_level=None, access_log=False, proxy_headers=False)
    # bounded graceful shutdown: open browser connections must not keep the process alive
    main = uvicorn.Server(uvicorn.Config(app, host=server.host, port=server.port, timeout_graceful_shutdown=5, **quiet))
    logging.getLogger("uvicorn.access").addFilter(_HideHealthchecks())
    loop = asyncio.get_running_loop()
    # a second later, so the answer to the request that asked for it still goes out
    runtime.restart_hook = lambda: loop.call_soon_threadsafe(loop.call_later, 1.0, setattr, main, "should_exit", True)
    if not server.tls_port:
        await main.serve()
        return
    cert = runtime.tls
    secure = _SecondServer(uvicorn.Config(app, host=server.host, port=server.tls_port, lifespan="off",
                                          ssl_certfile=str(cert.cert_path), ssl_keyfile=str(cert.key_path),
                                          timeout_graceful_shutdown=5, **quiet))
    logging.info("HTTPS for other apps on port %s, certificate SHA-256 %s", server.tls_port, cert.fingerprint)

    async def serve_secure() -> None:
        # a busy port (e.g. a UniFi controller on 8443) must not stop the app: uvicorn exits the process there
        try:
            await secure.serve()
        except (SystemExit, OSError):
            runtime.tls_error = f"Port {server.tls_port} ist belegt"
            logging.warning("HTTPS port %s is in use; other apps cannot connect (set OPENAMPERE_SERVER_TLS_PORT)",
                            server.tls_port)
        else:
            if not secure.started:
                runtime.tls_error = f"Port {server.tls_port} ließ sich nicht öffnen"

    secure_task = asyncio.create_task(serve_secure())
    try:
        await main.serve()
    finally:
        secure.should_exit = True
        await secure_task


def main() -> None:
    parser = argparse.ArgumentParser(description="OpenAmpere – local energy system app")
    parser.add_argument("command", nargs="?",
                        choices=["serve", "reset-password", "healthcheck", "ha-backup-copy", "ha-backup-done",
                                 "ha-integration"],
                        default="serve", help="reset-password: forget the access password (set a new one in the web "
                        "app); healthcheck: exit code 0 if the running server answers; ha-backup-copy / "
                        "ha-backup-done: before and after a backup of the Home Assistant app; ha-integration: "
                        "install its Home Assistant integration (at the start of the app, as root)")
    parser.add_argument("--config", help="path to config.yaml (default: $OPENAMPERE_CONFIG or ./config.yaml)")
    args = parser.parse_args()
    if args.command == "healthcheck":
        raise SystemExit(healthcheck(args.config))
    if args.command == "reset-password":
        import sqlite3

        from .config import build_config, read_yaml

        path = Path(build_config(read_yaml(args.config))[0].storage.path)
        if not path.is_file():
            raise SystemExit(f"Keine Datenbank unter {path} gefunden.")
        from .storage import schema_copies

        # plain connection on purpose: works while the app is running (SQLite handles other processes); also in the
        # copy an older version continues with after a rollback, which would otherwise bring back the old password
        for name in (path, *schema_copies(path)):
            db = sqlite3.connect(str(name), timeout=10)
            with db:
                db.execute("DELETE FROM meta WHERE key IN ('auth', 'sessions', 'known_devices', 'setup_locked')")
            db.close()
        print("Passwort zurückgesetzt. Beim nächsten Öffnen der App kann ein neues festgelegt werden.")
        return
    if args.command == "ha-integration":
        # runs as root (ha-app-start): reads nothing of the app user, writes only the integration; the result goes to
        # standard output, from there into the environment of OpenAmpere
        import json

        from . import ha_app

        print(json.dumps(ha_app.install_integration(ha_app.INTEGRATION_SOURCE, ha_app.HA_CONFIG)))
        return
    if args.command in ("ha-backup-copy", "ha-backup-done"):
        from . import ha_app
        from .config import build_config, read_yaml

        os.umask(0o077)  # like the database: only for the app user
        path = Path(build_config(read_yaml(args.config))[0].storage.path)
        if args.command == "ha-backup-copy":
            ha_app.write_backup_copy(path)
        else:
            ha_app.remove_backup_copy(path)
        return
    logs.setup()
    os.umask(0o077)  # the database, its copies and other files only for the app user (GHSA-p5pf-45jm-94c3)
    check_data_dir(args.config)
    from . import ha_app

    if ha_app.active():
        from .config import build_config, read_yaml

        if ha_app.adopt_backup_copy(Path(build_config(read_yaml(args.config))[0].storage.path)):
            logging.info("database taken from the restored Home Assistant backup")
    from .api import create_app
    from .runtime import Runtime
    from .storage import DatabaseInUse, SchemaError

    try:
        runtime = Runtime.from_files(args.config)
    except DatabaseInUse as err:
        raise SystemExit(f"OpenAmpere läuft bereits ({err}). Bitte die andere Instanz zuerst beenden.") from None
    except SchemaError as err:  # a database from a newer version without a copy, or no space for the copy (#166)
        raise SystemExit(str(err)) from None
    if not runtime.collector.configured:
        logging.info("no inverter configured yet – open the web app to run the setup")
    asyncio.run(serve(create_app(runtime), runtime))
    if runtime.restart_requested:  # e.g. a restored backup: the same command again, in this process (#165)
        logging.info("starting OpenAmpere again")
        runtime.storage.close()
        os.execv(sys.executable, sys.orig_argv)


if __name__ == "__main__":
    main()

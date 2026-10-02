"""Entry point: python -m openampere [--config config.yaml]"""

from __future__ import annotations

import argparse
import logging

import uvicorn

from .api import create_app
from .runtime import Runtime
from .storage import DatabaseInUse


def main() -> None:
    parser = argparse.ArgumentParser(description="OpenAmpere – local energy system app")
    parser.add_argument("command", nargs="?", choices=["serve", "reset-password"], default="serve",
                        help="reset-password: forget the access password (set a new one in the web app)")
    parser.add_argument("--config", help="path to config.yaml (default: $OPENAMPERE_CONFIG or ./config.yaml)")
    args = parser.parse_args()
    if args.command == "reset-password":
        import sqlite3
        from pathlib import Path

        from .config import build_config, read_yaml

        path = Path(build_config(read_yaml(args.config))[0].storage.path)
        if not path.is_file():
            raise SystemExit(f"Keine Datenbank unter {path} gefunden.")
        # plain connection on purpose: works while the app is running (SQLite handles other processes)
        db = sqlite3.connect(str(path), timeout=10)
        with db:
            db.execute("DELETE FROM meta WHERE key IN ('auth', 'sessions')")
        db.close()
        print("Passwort zurückgesetzt. Beim nächsten Öffnen der App kann ein neues festgelegt werden.")
        return
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    try:
        runtime = Runtime.from_files(args.config)
    except DatabaseInUse as err:
        raise SystemExit(f"OpenAmpere läuft bereits ({err}). Bitte die andere Instanz zuerst beenden.") from None
    if not runtime.collector.configured:
        logging.info("no inverter configured yet – open the web app to run the setup")
    server = runtime.config.server
    # bounded graceful shutdown: open browser connections must not keep the process alive
    uvicorn.run(create_app(runtime), host=server.host, port=server.port, log_level="info",
                timeout_graceful_shutdown=5)


if __name__ == "__main__":
    main()

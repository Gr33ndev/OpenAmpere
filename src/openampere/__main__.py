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
    parser.add_argument("--config", help="path to config.yaml (default: $OPENAMPERE_CONFIG or ./config.yaml)")
    args = parser.parse_args()
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

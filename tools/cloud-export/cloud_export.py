#!/usr/bin/env python3
"""Export all historical data of an installation from the former vendor cloud (unofficial tool).

Uses only the vendor's official, read-only customer API.
Raw JSON responses are stored unchanged, one file per endpoint and date, so an
interrupted export simply continues where it stopped.

The API blocks clients that poll too often; one request per minute is known to work.
Configuration via environment variables or a .env file (see README.md).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta
from pathlib import Path

DEFAULT_BASE_URL = "https://product.ekd-iot.de"  # customer API of the former vendor cloud
CLIENT_TYPE = "de.ekd.customer.apiclient"  # protocol constant required by the customer API

# (name, path suffix, extra query params). Ordered by importance for a data rescue.
DAILY_ENDPOINTS = [
    ("work", "/history/common/work", {"resolution": "15m"}),
    ("stateOfCharge", "/history/stateOfCharge", {"resolution": "15m"}),
    ("gridDraw", "/history/gridDraw/work", {"resolution": "15m"}),
    ("power", "/history/common/power", {"resolution": "15m"}),
    ("consumptionWork", "/history/consumption/work", {}),
    ("consumptionPower", "/history/consumption/power", {}),
    ("totalWork", "/total/common/work", {}),
]


class AuthError(Exception):
    pass


def log(msg: str) -> None:
    print(f"{datetime.now():%Y-%m-%d %H:%M:%S} {msg}", flush=True)


def load_env_file(path: Path) -> None:
    """Minimal .env reader; real environment variables take precedence."""
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


class Client:
    def __init__(self, base_url: str, api_key: str, min_interval: float, timeout: float) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.min_interval = min_interval
        self.timeout = timeout
        self._last = 0.0

    def _wait_turn(self) -> None:
        delay = self._last + self.min_interval - time.monotonic()
        if delay > 0:
            time.sleep(delay)
        self._last = time.monotonic()

    def get(self, path: str, params: dict | None = None) -> tuple[int, object]:
        """Returns (status, parsed body). Retries on throttling and transient errors."""
        url = self.base_url + path
        if params:
            url += "?" + urllib.parse.urlencode(params)
        request = urllib.request.Request(url, headers={
            "x-client-api-key": self.api_key,
            "x-client-type": CLIENT_TYPE,
            "accept": "application/json",
        })
        backoff = 300
        for attempt in range(1, 7):
            self._wait_turn()
            try:
                with urllib.request.urlopen(request, timeout=self.timeout) as response:
                    return response.status, json.loads(response.read() or b"null")
            except urllib.error.HTTPError as err:
                body = err.read()
                if err.code in (400, 404):
                    try:
                        return err.code, json.loads(body or b"null")
                    except ValueError:
                        return err.code, None
                if err.code == 401:
                    raise AuthError("API key rejected (HTTP 401)") from None
                # 403/429/5xx: possibly rate-limit block or server trouble -> back off and retry
                log(f"HTTP {err.code} for {path} (attempt {attempt}), waiting {backoff}s")
            except (urllib.error.URLError, TimeoutError, ConnectionError) as err:
                log(f"network error for {path}: {err} (attempt {attempt}), waiting {backoff}s")
            time.sleep(backoff)
            backoff = min(backoff * 2, 3600)
        raise RuntimeError(f"giving up on {path} after repeated failures")


def has_data(body: object) -> bool:
    """Heuristic: a response counts as data if it contains any non-null, non-zero number."""
    if isinstance(body, bool):
        return False
    if isinstance(body, (int, float)):
        return body != 0
    if isinstance(body, dict):
        return any(has_data(v) for k, v in body.items() if k not in ("date", "period", "resolution"))
    if isinstance(body, list):
        return any(has_data(v) for v in body)
    return False


def write_json(path: Path, status: int, body: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps({"status": status, "body": body}, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(path)


def resolve_installation(client: Client, out: Path) -> str:
    uuid = os.environ.get("CLOUD_INSTALLATION_UUID", "").strip()
    if uuid:
        return uuid
    status, body = client.get("/api/v1/customer/installation")
    write_json(out / "installations.json", status, body)
    if status != 200 or not isinstance(body, list) or not body:
        sys.exit("No installation returned; set CLOUD_INSTALLATION_UUID.")
    if len(body) > 1:
        sys.exit("Several installations found (see installations.json); set CLOUD_INSTALLATION_UUID.")
    return body[0]["uuid"]


def find_start(client: Client, base: str, inst_dir: Path) -> date:
    """Find the first month with data via yearly and monthly totals (few requests)."""
    cached = inst_dir / "start_date.txt"
    if cached.is_file():
        return date.fromisoformat(cached.read_text().strip())
    year = date.today().year
    first_year = None
    while year >= 2015:
        status, body = client.get(base + "/total/common/work", {"period": "year", "date": f"{year}-01-01"})
        write_json(inst_dir / "discovery" / f"year-{year}.json", status, body)
        if status == 200 and has_data(body):
            first_year = year
            year -= 1
        else:
            break
    if first_year is None:
        sys.exit("No data found in any year; check the installation.")
    start = date(first_year, 1, 1)
    for month in range(1, 13):
        if date(first_year, month, 1) > date.today():
            break
        status, body = client.get(base + "/total/common/work", {"period": "month", "date": f"{first_year}-{month:02d}-01"})
        write_json(inst_dir / "discovery" / f"month-{first_year}-{month:02d}.json", status, body)
        if status == 200 and has_data(body):
            start = date(first_year, month, 1)
            break
    cached.write_text(start.isoformat())
    return start


def cmd_check(client: Client, out: Path) -> None:
    uuid = resolve_installation(client, out)
    status, body = client.get(f"/api/v1/installation/{uuid}/now/all/power")
    log(f"installation found, now/all/power -> HTTP {status}")
    print(json.dumps(body, indent=1))


def cmd_export(client: Client, out: Path, start_override: str | None) -> None:
    uuid = resolve_installation(client, out)
    inst_dir = out / uuid
    base = f"/api/v1/installation/{uuid}"
    start = date.fromisoformat(start_override) if start_override else find_start(client, base, inst_dir)
    end = date.today() - timedelta(days=1)  # today is still incomplete
    days = [start + timedelta(days=n) for n in range((end - start).days + 1)]
    log(f"exporting {len(days)} days ({start} .. {end}) x {len(DAILY_ENDPOINTS)} endpoints")

    for name, suffix, extra in DAILY_ENDPOINTS:
        todo = [d for d in reversed(days) if not (inst_dir / name / f"{d}.json").exists()]
        log(f"[{name}] {len(days) - len(todo)} done, {len(todo)} to go (~{len(todo) * client.min_interval / 3600:.1f} h)")
        for i, d in enumerate(todo, 1):
            status, body = client.get(base + suffix, {"period": "day", "date": d.isoformat(), **extra})
            write_json(inst_dir / name / f"{d}.json", status, body)
            if status != 200 or i % 50 == 0:
                log(f"[{name}] {d} -> HTTP {status} ({i}/{len(todo)})")
    log("export complete")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("command", choices=["check", "export"], help="check: test key + live values; export: full history")
    parser.add_argument("--env-file", default=".env", help="path to .env file (default: ./.env)")
    parser.add_argument("--out", default=None, help="output directory (default: $CLOUD_EXPORT_DIR or ./data/cloud-export)")
    parser.add_argument("--start", help="first day YYYY-MM-DD (skips automatic detection)")
    args = parser.parse_args()

    load_env_file(Path(args.env_file))
    api_key = os.environ.get("CLOUD_API_KEY", "").strip()
    if not api_key:
        sys.exit("CLOUD_API_KEY is not set (environment or .env file).")
    client = Client(
        base_url=os.environ.get("CLOUD_API_BASE_URL", DEFAULT_BASE_URL),
        api_key=api_key,
        min_interval=float(os.environ.get("CLOUD_MIN_INTERVAL", "65")),
        timeout=float(os.environ.get("CLOUD_API_TIMEOUT", "30")),
    )
    out = Path(args.out or os.environ.get("CLOUD_EXPORT_DIR", "data/cloud-export"))
    try:
        if args.command == "check":
            cmd_check(client, out)
        else:
            cmd_export(client, out, args.start)
    except AuthError as err:
        sys.exit(str(err))
    except KeyboardInterrupt:
        log("interrupted; run again to resume")


if __name__ == "__main__":
    main()

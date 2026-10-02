"""Find and test inverters in the local network (setup wizard)."""

from __future__ import annotations

import asyncio
import ipaddress
import logging
import socket

from .drivers import registry
from .drivers.modbus import DeviceUnreachable, friendly_error
from .runtime import Runtime

log = logging.getLogger(__name__)


def local_prefixes() -> list[str]:
    """Best guess of the local /24 networks, e.g. ["192.168.178"]. Ignores Docker's default bridge."""
    addresses = set()
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("10.255.255.255", 1))  # no traffic is sent
            addresses.add(s.getsockname()[0])
    except OSError:
        pass
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            addresses.add(info[4][0])
    except OSError:
        pass
    prefixes = []
    for address in addresses:
        ip = ipaddress.ip_address(address)
        if ip.is_private and not ip.is_loopback and not address.startswith("172.17."):
            prefixes.append(address.rsplit(".", 1)[0])
    return sorted(set(prefixes))


def parse_prefix(prefix: str) -> list[str]:
    """'192.168.178' or '192.168.178.0/24' -> host addresses (max /22 to keep scans short)."""
    prefix = prefix.strip()
    if "/" not in prefix:
        prefix = prefix.rstrip(".")
        if prefix.count(".") != 2:
            raise ValueError("Netz bitte als z. B. 192.168.178 angeben")
        prefix += ".0/24"
    network = ipaddress.ip_network(prefix, strict=False)
    if not network.is_private or network.num_addresses > 1024:
        raise ValueError("Nur private Netze bis /22 können durchsucht werden")
    return [str(h) for h in network.hosts()]


async def _port_open(host: str, port: int, timeout: float) -> bool:
    try:
        _, writer = await asyncio.wait_for(asyncio.open_connection(host, port), timeout)
        writer.close()
        return True
    except (OSError, asyncio.TimeoutError):
        return False


async def scan(runtime: Runtime, prefix: str, port: int = 502, unit: int = 0) -> list[dict]:
    hosts = parse_prefix(prefix)
    semaphore = asyncio.Semaphore(128)

    async def probe(host: str) -> str | None:
        async with semaphore:
            return host if await _port_open(host, port, 0.7) else None

    found = [h for h in await asyncio.gather(*(probe(h) for h in hosts)) if h]
    collector, current = runtime.collector, runtime.config.inverter
    results = []
    for host in found:
        if collector.connected and collector.device and host == current.host and port == current.port:
            device = collector.device  # already connected: do not open a second connection
        else:
            try:
                device = await registry.detect(host, port, unit)
            except ConnectionError:
                device = None
        results.append({"host": host, "port": port, "model": device.model if device else None,
                        "manufacturer": device.manufacturer if device else None,
                        "driver": device.driver if device else None, "unit": device.unit if device else None})
    return results


async def test_connection(runtime: Runtime, host: str, port: int, unit: int, driver: str = "auto") -> dict:
    collector, current = runtime.collector, runtime.config.inverter
    if (collector.connected and collector.device and (host, port) == (current.host, current.port)
            and driver in ("auto", collector.device.driver) and unit in (0, collector.device.unit)):
        return {"ok": True, "device": collector.device.__dict__}
    timeout = max(3.0, runtime.config.inverter.timeout)
    try:
        info, device = await registry.detect_driver(host, port, unit, timeout=timeout,
                                                    only=None if driver == "auto" else driver)
    except DeviceUnreachable:
        return {"ok": False, "error": f"Keine Verbindung zu {host}:{port}. Stimmt die Adresse und ist Modbus TCP aktiviert?"}
    except (ConnectionError, OSError, asyncio.TimeoutError) as err:
        return {"ok": False, "error": friendly_error(err)}
    try:
        snap = await device.read()
        sample = {"pv_power": snap.pv_power, "battery_soc": snap.battery_soc}
    except Exception:  # identification worked; a failed sample read is not fatal
        sample = None
    finally:
        await device.close()
    return {"ok": True, "device": info.__dict__, "label": registry.LABELS.get(info.driver), "sample": sample}


REDISCOVER_AFTER_S = 180  # unreachable this long -> look for the device elsewhere in the network
REDISCOVER_EVERY_S = 1800


async def find_by_serial(runtime: Runtime, prefix: str, port: int, serial: str) -> str | None:
    """Host in the /24 network that answers as the device with this serial number (read-only probes)."""
    hosts = parse_prefix(prefix)
    semaphore = asyncio.Semaphore(128)

    async def probe(host: str) -> str | None:
        async with semaphore:
            return host if await _port_open(host, port, 0.7) else None

    for host in [h for h in await asyncio.gather(*(probe(h) for h in hosts)) if h]:
        try:
            info = await registry.detect(host, port, runtime.config.inverter.unit,
                                         timeout=runtime.config.inverter.timeout)
        except (ConnectionError, OSError, asyncio.TimeoutError):
            continue
        if info.serial and info.serial == serial:
            return host
    return None


class Rediscovery:
    """Remembers the connected device's serial number. If it becomes unreachable (typically a new IP
    address from the router's DHCP), searches the local network for it and switches over."""

    def __init__(self, runtime: Runtime) -> None:
        self.runtime = runtime
        self._last_attempt = float("-inf")

    def remember(self) -> None:
        collector = self.runtime.collector
        device = collector.device
        if collector.connected and device and device.serial:
            known = {"serial": device.serial, "host": self.runtime.config.inverter.host}
            if self.runtime.storage.get_meta("known_device") != known:
                self.runtime.storage.set_meta("known_device", known)

    async def check(self, now: float) -> str | None:
        runtime, collector = self.runtime, self.runtime.collector
        self.remember()
        known = runtime.storage.get_meta("known_device")
        inverter = runtime.config.inverter
        if (collector.connected or not known or "inverter.host" in runtime.locked
                or known.get("host") != inverter.host or inverter.host.count(".") != 3):
            return None
        since = collector.disconnected_since
        if since is None or now - since < REDISCOVER_AFTER_S or now - self._last_attempt < REDISCOVER_EVERY_S:
            return None
        self._last_attempt = now
        prefix = inverter.host.rsplit(".", 1)[0]
        log.info("inverter unreachable since %.0f s - searching %s.0/24 for serial %s", now - since, prefix,
                 known["serial"])
        host = await find_by_serial(runtime, prefix, inverter.port, known["serial"])
        if host is None or host == inverter.host:
            return None
        log.warning("inverter found at new address %s (was %s)", host, inverter.host)
        runtime.storage.set_meta("relocated", {"from": inverter.host, "to": host, "ts": now})
        await runtime.update_settings({"inverter.host": host})
        return host

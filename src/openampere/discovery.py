"""Find and test inverters in the local network (setup wizard)."""

from __future__ import annotations

import asyncio
import ipaddress
import socket

from .drivers import registry
from .drivers.modbus import DeviceUnreachable
from .runtime import Runtime


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
        return {"ok": False, "error": str(err)}
    try:
        snap = await device.read()
        sample = {"pv_power": snap.pv_power, "battery_soc": snap.battery_soc}
    except Exception:  # identification worked; a failed sample read is not fatal
        sample = None
    finally:
        await device.close()
    return {"ok": True, "device": info.__dict__, "label": registry.LABELS.get(info.driver), "sample": sample}

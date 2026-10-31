# SPDX-FileCopyrightText: Copyright the OpenAmpere contributors
# SPDX-License-Identifier: MIT
"""Wallboxes OpenAmpere talks to itself, without evcc (#318).

Every wallbox is a module (see base.Wallbox) listed in WALLBOXES, the same way as the grid operators in gridmeter/.
This is the read-only check for the setup (#325); the wallboxes OpenAmpere reads (and maybe controls) all the time are
in native.py. The check reads in the wallbox's current mode, never writes, and
its last result goes into the diagnostics report, so the registers can be confirmed on real devices before OpenAmpere
controls anything. Adding a wallbox: a module with its blocks and interpret(), an entry here, simulator answers and a
test.
"""

from __future__ import annotations

from dataclasses import asdict

from ..privacy import masked_block
from .. import ha_app
from ..probe import Probe, ProbeBusy, ProbeError, ProbeUnreachable, ProbeUnresolved, blocks_markdown, is_local_name
from .base import Wallbox
from .foxess import FoxEssCharger
from .weidmueller import WeidmuellerAcSmart

WALLBOXES: dict[str, type[Wallbox]] = {w.key: w for w in (FoxEssCharger, WeidmuellerAcSmart)}
APP_MODES = ("default", "modbus_tcp", "ocpp", "unknown")  # what the manufacturer's app shows, as the owner says
WIRED = ("yes", "no", "unknown")  # wired to the inverter (the manufacturer's own solar linkage)

__all__ = ["APP_MODES", "WALLBOXES", "WIRED", "Wallbox", "WallboxCheck", "check_markdown"]


class WallboxCheck:
    def __init__(self, probe: Probe) -> None:
        self.probe = probe
        self._last: dict | None = None  # with all values; masked in section()

    async def run(self, key: str, host: str, port: int | None, unit: int | None, app_mode: str = "unknown",
                  wired: str = "unknown") -> dict:
        wallbox = WALLBOXES.get(key)
        if wallbox is None:
            raise ProbeError("Diese Wallbox kennt OpenAmpere noch nicht.")
        candidates = (port,) if port else wallbox.ports
        # the inverter's own port is read over its connection with its unit id: most likely the wrong address (#411).
        # Another port of the same address can be another device, e.g. behind a Modbus gateway (#417).
        if self.probe.runtime.config.inverter.port in candidates and await self.probe.is_inverter(host.strip()):
            raise ProbeError("Das ist die Adresse deines Wechselrichters. Bitte gib die Adresse der Wallbox ein, du "
                             "findest sie zum Beispiel in der Geräteliste deines Routers.")
        unit = unit or wallbox.unit
        result = {"model": key, "label": wallbox.label, "host": host.strip(), "port": None, "unit": unit,
                  "outcome": "unreachable", "tried_ports": [], "ports": {}, "local_name_in_app": False,
                  "blocks": [], "findings": {},
                  "answers": {"app_mode": app_mode if app_mode in APP_MODES else "unknown",
                              "wired": wired if wired in WIRED else "unknown"}}
        read = None
        for candidate in candidates:
            result["tried_ports"].append(candidate)
            try:
                read = await self.probe.read_ranges(host, candidate, unit, wallbox.function, list(wallbox.ranges))
            except ProbeUnresolved:  # before any connection: no port was tried (#427)
                result["tried_ports"].clear()
                result["outcome"] = "unresolved"
                result["local_name_in_app"] = ha_app.active() and is_local_name(host.strip())
                break
            except ProbeUnreachable as err:  # "refused" | "timeout" | "open", per port for the report (#427)
                result["ports"][str(candidate)] = err.reason
                continue
            except ProbeBusy:
                result["port"], result["outcome"] = candidate, "connections_taken"
                break
            result["port"] = candidate
            break
        if read is None and result["outcome"] == "unreachable" and set(result["ports"].values()) - {"timeout"}:
            # the address answers, but no port takes a Modbus connection: wrong device, or another client (the
            # previous smartbox, evcc) holds the only one (#427)
            result["outcome"] = "connection_refused"
        if read is not None:
            first = read.blocks[0]
            result["outcome"] = ("readable" if first.ok else "busy" if first.exception == 6
                                 else "rejected" if first.error == "rejected" else "no_answer")
            values = {b.start + i: w for b in read.blocks if b.ok for i, w in enumerate(b.words or [])}
            result["findings"] = wallbox.interpret(values) if values else {}
            result["blocks"] = [asdict(b) for b in read.blocks]
        self._last = result
        return self.section(include_text=True)  # the owner's own screen: private values are still left out

    def section(self, include_text: bool = False) -> dict | None:
        """The last check for the diagnostics report, masked by the rules of privacy.py like every report."""
        if self._last is None:
            return None
        wallbox = WALLBOXES[self._last["model"]]
        return {**self._last, "blocks": [masked_block(b, include_text, wallbox.private, wallbox.public_text)
                                         for b in self._last["blocks"]]}


OUTCOME = {
    "readable": "lesbar im aktuellen Modus",
    "busy": "Modbus abgelehnt, Gerät beschäftigt (Ausnahme 6): vermutlich eine andere Steuerungsart",
    "rejected": "Anfrage abgelehnt: andere Registerkarte oder Firmware",
    "no_answer": "keine Antwort auf die Anfrage",
    "connections_taken": "Verbindung sofort wieder geschlossen: alle Verbindungen belegt",
    "unresolved": "Name nicht auflösbar, keine Verbindung versucht",
    "connection_refused": "Gerät erreichbar, nimmt aber keine Modbus-Verbindung an",
    "unreachable": "keine Verbindung: kein Gerät antwortet unter dieser Adresse",
}
PORT_STATE = {  # why a port gave no Modbus connection (#427)
    "refused": "abgelehnt",
    "timeout": "keine Antwort",
    "open": "Verbindung angenommen, Modbus-Verbindung aber nicht",
}


def check_markdown(check: dict) -> list[str]:
    """The check's part of the diagnostics report as text for an issue (German, like the rest of the report)."""
    answers = check["answers"]
    lines = [f"- Gewählt: {check['label']}, Adresse {check['host']}, Ports {', '.join(map(str, check['tried_ports'])) or '–'}"
             f", Geräteadresse {check['unit']}",
             f"- Ergebnis: {OUTCOME.get(check['outcome'], check['outcome'])}"
             + (" (Name auf .local in der Home-Assistant-App)" if check.get("local_name_in_app") else ""),
             f"- Angaben des Besitzers: Steuerungsart in der App: {answers['app_mode']}; mit dem Wechselrichter "
             f"verkabelt: {answers['wired']}"]
    ports = check.get("ports") or {}
    if ports:
        lines.append("- Verbindung je Port: " + ", ".join(f"{port} {PORT_STATE.get(state, state)}"
                                                         for port, state in ports.items()))
    findings = {k: v for k, v in check["findings"].items() if v is not None}
    if findings:
        lines.append("- Erkannt: " + ", ".join(f"{k} {v}" for k, v in findings.items()))
    return lines + blocks_markdown(check["blocks"], "Registerwerte der Wallbox")

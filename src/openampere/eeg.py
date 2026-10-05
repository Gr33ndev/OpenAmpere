"""Feed-in compensation under the German EEG for rooftop solar (#71).

The fixed compensation depends on when the plant was commissioned and on its installed power. Power above 10 and
40 kW gets lower rates, but not for "the last kWh": § 23c EEG splits every kWh by the share of the power in each zone.
A 12 kWp plant gets 10/12 of the rate up to 10 kW and 2/12 of the rate from 10 to 40 kW for every kWh fed in. Since
30.07.2022 plants that feed in all their energy (Volleinspeisung) get higher rates (§ 48 Abs. 2a EEG).

Scope: buildings and noise barriers up to 100 kWp, fixed compensation (no direct marketing), commissioned from 2018.
Rates are the compensation actually paid (the "anzulegender Wert" minus 0.4 ct, § 53 EEG), rounded to 0.01 ct as
grid operators bill them. Source: the Bundesnetzagentur's tables per commissioning period (EEG-Förderung, Archiv der
Vergütungssätze, Nov 2017 to Dec 2026), cross-checked with the tables of the SFV.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

ZONES_KW = ((0, 10), (10, 40), (40, 100))
# Commissioned from this day: rates (ct/kWh) for the zones up to 10, 40 and 100 kW for partial feed-in
# (Überschusseinspeisung) and full feed-in (Volleinspeisung; None = no own rate, the partial ones apply).
# Each row is valid until the day before the next one, the last one until LAST_DAY.
PERIODS: tuple[tuple[str, tuple[float, float, float], tuple[float, float, float] | None], ...] = (
    ("2018-01-01", (12.20, 11.87, 10.61), None),
    ("2018-08-01", (12.08, 11.74, 10.50), None),
    ("2018-09-01", (11.95, 11.62, 10.39), None),
    ("2018-10-01", (11.83, 11.50, 10.28), None),
    ("2018-11-01", (11.71, 11.38, 10.17), None),
    ("2018-12-01", (11.59, 11.27, 10.07), None),
    ("2019-01-01", (11.47, 11.15, 9.96), None),
    ("2019-02-01", (11.35, 11.03, 9.47), None),
    ("2019-03-01", (11.23, 10.92, 8.99), None),
    ("2019-04-01", (11.11, 10.81, 8.50), None),
    ("2019-05-01", (10.95, 10.65, 8.38), None),
    ("2019-06-01", (10.79, 10.50, 8.25), None),
    ("2019-07-01", (10.64, 10.34, 8.13), None),
    ("2019-08-01", (10.48, 10.19, 8.01), None),
    ("2019-09-01", (10.33, 10.04, 7.89), None),
    ("2019-10-01", (10.18, 9.90, 7.78), None),
    ("2019-11-01", (10.08, 9.79, 7.70), None),
    ("2019-12-01", (9.97, 9.69, 7.62), None),
    ("2020-01-01", (9.87, 9.59, 7.54), None),
    ("2020-02-01", (9.72, 9.45, 7.42), None),
    ("2020-03-01", (9.58, 9.31, 7.31), None),
    ("2020-04-01", (9.44, 9.18, 7.21), None),
    ("2020-05-01", (9.30, 9.04, 7.10), None),
    ("2020-06-01", (9.17, 8.91, 7.00), None),
    ("2020-07-01", (9.03, 8.78, 6.89), None),
    ("2020-08-01", (8.90, 8.65, 6.79), None),
    ("2020-09-01", (8.77, 8.53, 6.69), None),
    ("2020-10-01", (8.64, 8.40, 6.59), None),
    ("2020-11-01", (8.48, 8.24, 6.46), None),
    ("2020-12-01", (8.32, 8.09, 6.34), None),
    ("2021-01-01", (8.16, 7.93, 6.22), None),
    ("2021-02-01", (8.04, 7.81, 6.13), None),
    ("2021-03-01", (7.92, 7.70, 6.04), None),
    ("2021-04-01", (7.81, 7.59, 5.95), None),
    ("2021-05-01", (7.69, 7.47, 5.86), None),
    ("2021-06-01", (7.58, 7.36, 5.77), None),
    ("2021-07-01", (7.47, 7.25, 5.68), None),
    ("2021-08-01", (7.36, 7.15, 5.60), None),
    ("2021-09-01", (7.25, 7.04, 5.51), None),
    ("2021-10-01", (7.14, 6.94, 5.43), None),
    ("2021-11-01", (7.03, 6.83, 5.35), None),
    ("2021-12-01", (6.93, 6.73, 5.27), None),
    ("2022-01-01", (6.83, 6.63, 5.19), None),
    ("2022-02-01", (6.73, 6.53, 5.11), None),
    ("2022-03-01", (6.63, 6.44, 5.03), None),
    ("2022-04-01", (6.53, 6.34, 4.96), None),
    ("2022-05-01", (6.43, 6.25, 4.88), None),
    ("2022-06-01", (6.34, 6.15, 4.81), None),
    ("2022-07-01", (6.24, 6.06, 4.74), None),
    ("2022-07-30", (8.20, 7.10, 5.80), (13.00, 10.90, 10.90)),
    ("2024-02-01", (8.11, 7.03, 5.74), (12.87, 10.79, 10.79)),
    ("2024-08-01", (8.03, 6.95, 5.68), (12.73, 10.68, 10.68)),
    ("2025-02-01", (7.94, 6.88, 5.62), (12.60, 10.56, 10.56)),
    ("2025-08-01", (7.86, 6.80, 5.56), (12.47, 10.45, 10.45)),
    ("2026-02-01", (7.78, 6.73, 5.50), (12.34, 10.35, 10.35)),
    ("2026-08-01", (7.70, 6.66, 5.44), (12.22, 10.24, 10.24)),
)
# Last commissioning day with published rates. Later rates are not guessed: the EEG 2023 applies until the end of
# 2026 and the rules after that are not decided yet.
LAST_DAY = "2026-12-31"
MAX_KWP = 100.0


@dataclass
class FeedInRate:
    ct: float  # compensation per kWh fed in, weighted over the zones
    period_from: str  # commissioning period the rates belong to
    period_to: str
    full: bool  # rates for full feed-in
    zones: list[dict]  # [{"from_kw", "to_kw", "kw", "share", "ct"}] for the zones the plant reaches
    funding_until: str  # last day with compensation: 31.12. of the 20th year after commissioning (§ 25 EEG)


def rate(commissioned: date, kwp: float, full: bool = False) -> FeedInRate:
    """Compensation per kWh for a plant; raises ValueError with a German message when it is out of scope."""
    if not kwp or kwp <= 0:
        raise ValueError("Bitte die installierte Modulleistung angeben.")
    if kwp > MAX_KWP:
        raise ValueError("Anlagen über 100 kWp vermarkten ihren Strom direkt. Bitte die Vergütung von Hand eintragen.")
    day = commissioned.isoformat()
    if day < PERIODS[0][0]:
        raise ValueError("Für Anlagen, die vor 2018 in Betrieb gingen, kennt OpenAmpere die Sätze nicht. "
                         "Bitte die Vergütung von Hand eintragen.")
    if day > LAST_DAY:
        raise ValueError("Für dieses Inbetriebnahmedatum sind noch keine Sätze veröffentlicht. "
                         "Bitte die Vergütung von Hand eintragen.")
    index = max(i for i, row in enumerate(PERIODS) if row[0] <= day)
    start, partial, full_rates = PERIODS[index]
    end = (date.fromisoformat(PERIODS[index + 1][0]).toordinal() - 1 if index + 1 < len(PERIODS)
           else date.fromisoformat(LAST_DAY).toordinal())
    rates = full_rates if full and full_rates else partial
    zones = []
    for (low, high), ct in zip(ZONES_KW, rates):
        kw = max(0.0, min(kwp, high) - low)
        if kw > 0:
            zones.append({"from_kw": low, "to_kw": high, "kw": round(kw, 3), "share": kw / kwp, "ct": ct})
    return FeedInRate(ct=sum(z["share"] * z["ct"] for z in zones), period_from=start,
                      period_to=date.fromordinal(end).isoformat(), full=bool(full and full_rates), zones=zones,
                      funding_until=f"{commissioned.year + 20}-12-31")

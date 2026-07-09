#!/usr/bin/env python3
"""Match each city to its nearest usable observed weather station (Meteostat,
which wraps NOAA ISD + GHCN-Daily). Writes scripts/stations.json — a reviewed,
committed mapping so the pipeline never re-queries the station network at
build time. A city with no current station within MAX_KM is single-source
(ERA5 only) and recorded as null."""

from __future__ import annotations

import json
import math
import sys
from pathlib import Path

from meteostat import Stations

HERE = Path(__file__).resolve().parent
CITIES = HERE / "cities.json"
OUT = HERE / "stations.json"

MAX_KM = 35.0
NEEDS_RECENT_YEAR = 2024   # station must still report into this year or later
WANTS_START_YEAR = 1995    # prefer stations whose daily record reaches this far back


def main() -> int:
    cities = json.loads(CITIES.read_text())
    mapping = {}
    covered = deep = 0
    for city in cities:
        near = Stations().nearby(city["latitude"], city["longitude"]).fetch(8)
        pick = None
        for sid, row in near.iterrows():
            end = row.get("daily_end")
            start = row.get("daily_start")
            dist_km = row["distance"] / 1000.0
            if end is None or (hasattr(end, "year") is False):
                continue
            try:
                end_year = end.year
                start_year = start.year if start is not None and hasattr(start, "year") else 9999
            except (AttributeError, ValueError):
                continue
            if dist_km <= MAX_KM and end_year >= NEEDS_RECENT_YEAR:
                pick = {
                    "station_id": str(sid),
                    "name": str(row["name"]),
                    "distance_km": round(dist_km, 1),
                    "daily_start": str(start.date()) if start is not None and hasattr(start, "date") else None,
                    "daily_end": str(end.date()),
                }
                break
        mapping[city["id"]] = pick
        if pick:
            covered += 1
            if pick["daily_start"] and pick["daily_start"][:4] <= str(WANTS_START_YEAR):
                deep += 1
            print(f"  {city['id']:16s} {pick['name'][:32]:32s} {pick['distance_km']:5.1f}km  {pick['daily_start']}→{pick['daily_end']}", file=sys.stderr)
        else:
            print(f"  {city['id']:16s} NO STATION (single-source, ERA5 only)", file=sys.stderr)

    OUT.write_text(json.dumps(mapping, indent=2) + "\n", encoding="utf-8")
    print(f"\nWrote {OUT}: {covered}/{len(cities)} cities have a station ({deep} reach {WANTS_START_YEAR} or earlier)", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

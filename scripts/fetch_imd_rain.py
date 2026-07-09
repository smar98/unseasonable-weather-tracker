#!/usr/bin/env python3
"""Fetch IMD gridded daily rainfall (0.25°, Pai et al. 2014) and extract the
nearest grid cell for each city — an independent, India-official rainfall
source to cross-check ERA5's heavy-rain flags.

IMD gridded rainfall is interpolated from IMD's gauge network (station-based,
not a model), so it is genuinely independent of ERA5 reanalysis. It covers the
whole country — including cities with no NOAA station — but only rainfall, and
the archive currently ends in 2025. Downloaded free via the imdlib package
(imdpune.gov.in). Cite: Pai et al. (2014), MAUSAM 65(1). Writes
imd_cache/<city_id>.csv.gz (date, rain_mm).

Usage: python3 scripts/fetch_imd_rain.py [--since YEAR]   (default 2015)
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import gzip
import io
import sys
import tempfile
from pathlib import Path

import json
import imdlib as imd

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
CITIES = HERE / "cities.json"
IMD_DIR = REPO / "imd_cache"

FILL_LO, FILL_HI = -100.0, 5000.0  # valid rainfall band; IMD fills no-data with -999


def read_cache(city_id: str) -> dict[str, float]:
    path = IMD_DIR / f"{city_id}.csv.gz"
    if not path.exists():
        return {}
    with gzip.open(path, "rt", encoding="utf-8") as handle:
        return {r["date"]: float(r["rain_mm"]) for r in csv.DictReader(handle) if r["rain_mm"] != ""}


def write_cache(city_id: str, series: dict[str, float]) -> None:
    IMD_DIR.mkdir(parents=True, exist_ok=True)
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(["date", "rain_mm"])
    for date in sorted(series):
        writer.writerow([date, series[date]])
    with open(IMD_DIR / f"{city_id}.csv.gz", "wb") as raw:
        with gzip.GzipFile(fileobj=raw, mode="wb", mtime=0) as handle:
            handle.write(buffer.getvalue().encode("utf-8"))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--since", type=int, default=2015)
    args = parser.parse_args()

    cities = json.loads(CITIES.read_text())
    last_year = dt.date.today().year
    series: dict[str, dict[str, float]] = {c["id"]: read_cache(c["id"]) for c in cities}

    with tempfile.TemporaryDirectory() as tmp:
        for year in range(args.since, last_year + 1):
            try:
                data = imd.get_data("rain", year, year, fn_format="yearwise", file_dir=tmp)
                ds = data.get_xarray()
            except Exception as error:  # a year not yet published (e.g. current year) — skip
                print(f"  {year}: unavailable ({str(error)[:60]})", file=sys.stderr)
                continue
            times = [str(t)[:10] for t in ds.time.values]
            for city in cities:
                pt = ds.sel(lat=city["latitude"], lon=city["longitude"], method="nearest")
                vals = pt["rain"].values
                cser = series[city["id"]]
                for date, v in zip(times, vals):
                    fv = float(v)
                    if FILL_LO < fv < FILL_HI:
                        cser[date] = round(fv, 1)
            print(f"  {year}: extracted {len(times)} days for {len(cities)} cities", file=sys.stderr)

    written = 0
    for city in cities:
        if series[city["id"]]:
            write_cache(city["id"], series[city["id"]])
            written += 1
    print(f"\nWrote IMD rainfall for {written} cities to {IMD_DIR}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

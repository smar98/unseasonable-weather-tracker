#!/usr/bin/env python3
"""Fetch observed daily station data (Meteostat → NOAA ISD/GHCN) for every
city that has a matched station in stations.json. This is the independent
ground-truth layer: real measurements to check ERA5 against, not another
model. Writes obs_cache/<city_id>.csv.gz (date, tmax, tmin, prcp).

Runs in GitHub Actions like the ERA5 refresh; Meteostat is free (CC-BY) and
needs no key. Temperature is the rigorous cross-check; precipitation is
present but sparser and treated as wet/dry corroboration only."""

from __future__ import annotations

import csv
import datetime as dt
import gzip
import io
import sys
from pathlib import Path

from meteostat import Daily
import pandas as pd
import json

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
STATIONS = HERE / "stations.json"
OBS_DIR = REPO / "obs_cache"
OBS_START = dt.datetime(2010, 1, 1)  # enough overlap for events + an agreement rate


def write_gz(path: Path, rows: list[dict]) -> None:
    OBS_DIR.mkdir(parents=True, exist_ok=True)
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(["date", "tmax", "tmin", "prcp"])
    for r in rows:
        writer.writerow([r["date"], r["tmax"], r["tmin"], r["prcp"]])
    with open(path, "wb") as raw:
        with gzip.GzipFile(fileobj=raw, mode="wb", mtime=0) as handle:
            handle.write(buffer.getvalue().encode("utf-8"))


def main() -> int:
    stations = json.loads(STATIONS.read_text())
    end = dt.datetime.combine(dt.date.today(), dt.time())
    covered = 0
    for city_id, st in stations.items():
        if not st:
            continue
        try:
            df = Daily(st["station_id"], OBS_START, end).fetch()
        except Exception as error:
            print(f"  {city_id}: fetch failed ({error})", file=sys.stderr)
            continue
        rows = []
        for ts, row in df.iterrows():
            def clean(v):
                return "" if v is None or pd.isna(v) else round(float(v), 1)
            rows.append({
                "date": ts.date().isoformat(),
                "tmax": clean(row.get("tmax")),
                "tmin": clean(row.get("tmin")),
                "prcp": clean(row.get("prcp")),
            })
        write_gz(OBS_DIR / f"{city_id}.csv.gz", rows)
        n_temp = sum(1 for r in rows if r["tmax"] != "")
        covered += 1
        print(f"  {city_id:16s} {st['name'][:28]:28s} {len(rows):5d} days, {n_temp} with tmax", file=sys.stderr)
    print(f"\nWrote observed data for {covered} cities to {OBS_DIR}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

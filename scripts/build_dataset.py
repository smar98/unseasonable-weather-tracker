#!/usr/bin/env python3
"""Build per-city seasonal-anomaly datasets from ERA5 reanalysis (v2).

Method summary (see docs/methodology.md for the full treatment):

- Source: Open-Meteo archive API, model `era5_seamless` (ERA5-Land ~9 km
  blended with ERA5 ~31 km; snowfall comes from ERA5 because ERA5-Land does
  not provide it through this API).
- Indices follow the ETCCDI percentile-index family (Zhang et al. 2011):
  warm days (tmax above the seasonal 90th percentile), hot extremes (95th),
  cold nights / cold extremes (tmin below the 10th / 5th), and heavy
  precipitation on wet days (>= 1 mm) above the seasonal 95th percentile.
- Seasonal comparison uses a 5-day window centred on each calendar day
  (ETCCDI convention), pooled across the 1991-2020 baseline: ~150 values.
- Percentiles are midrank-based exceedance fractions, not interpolated
  quantiles: pct(x) = (#pool < x + 0.5 * #pool == x) / n. Every flag is
  therefore reportable as "exceeded k of n comparable baseline days".
- For days inside the baseline period the pooled values from the same year
  are excluded (leave-one-year-out) so in-base years are judged against the
  other 29 years. The full Zhang et al. (2005) bootstrap is noted in the
  methodology as the exact treatment; LOO is the practical approximation.
- Snow (snow-capable cities only) has two amount-aware flags instead of a
  composite score: rare-occurrence snow (baseline probability of a >= 1 cm
  snow day in the seasonal window <= 5%) and exceptional snow amount
  (above the seasonal 95th percentile of baseline snow-day amounts).
- Outputs: public/data/index.json plus public/data/cities/<id>.json.
  Raw daily values are cached in data_cache/<id>.csv.gz so the daily
  refresh only fetches the trailing days.
"""

from __future__ import annotations

import argparse
import bisect
import csv
import datetime as dt
import gzip
import io
import json
import math
import statistics
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
CITIES_FILE = Path(__file__).resolve().parent / "cities.json"
CACHE_DIR = REPO_ROOT / "data_cache"
OUTPUT_DIR = REPO_ROOT / "public" / "data"
VALIDATION_FILE = REPO_ROOT / "docs" / "validation-register.json"
STATIONS_FILE = Path(__file__).resolve().parent / "stations.json"
OBS_DIR = REPO_ROOT / "obs_cache"
IMD_DIR = REPO_ROOT / "imd_cache"

# how close ERA5's value must sit to the nearest station's observed value to
# call the flag "observation-confirmed". Station and ~9-31 km grid cell
# legitimately differ by a few degrees, so this is a tolerance on the value,
# not an exact match — the test is "did ERA5 get the day about right?"
OBS_TEMP_TOL_C = 3.0
OBS_WET_MM = 1.0

API_URL = "https://archive-api.open-meteo.com/v1/archive"
MODEL = "era5_seamless"
DAILY_VARIABLES = "temperature_2m_max,temperature_2m_min,precipitation_sum,snowfall_sum"

FULL_START = dt.date(1960, 1, 1)
BASE_START = dt.date(1991, 1, 1)
BASE_END = dt.date(2020, 12, 31)
RECENT_LAG_DAYS = 5          # reanalysis publication lag
HALF_WINDOW_DAYS = 2         # ETCCDI 5-day centred window
WET_DAY_MM = 1.0             # ETCCDI wet-day definition
SNOW_DAY_CM = 1.0            # meaningful snow; ERA5 trace amounts excluded
MIN_POOL_WET = 15            # minimum wet-day pool for a heavy-precip flag
MIN_POOL_SNOW = 15           # minimum snow-day pool for an exceptional-snow flag
RARE_SNOW_MAX_PROB = 0.05
SNOW_CAPABLE_MIN_BASE_DAYS = 30  # baseline snow days (>= 1 cm) to enable snow flags

WARM_PCT = 0.90
HOT_PCT = 0.95
COLD_PCT = 0.10
COLD_EXTREME_PCT = 0.05
HEAVY_PRECIP_PCT = 0.95
EXCEPTIONAL_SNOW_PCT = 0.95

EVENT_YEARS = 3              # window for the candidate-event table
TOP_EVENTS_PER_CATEGORY = 6

SCHEMA_VERSION = "2.0.0"


# --------------------------------------------------------------------------
# Fetching and caching
# --------------------------------------------------------------------------

def http_get_json(url: str, attempts: int = 8, backoff: float = 3.0) -> dict[str, Any]:
    last_error: Exception | None = None
    for attempt in range(attempts):
        try:
            with urllib.request.urlopen(url, timeout=120) as response:
                return json.load(response)
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as error:
            last_error = error
            # long historical pulls are call-weighted: a 429 means the minute/
            # hour quota is spent, so short retries are useless — wait it out
            rate_limited = isinstance(error, urllib.error.HTTPError) and error.code == 429
            wait = min(75 * (attempt + 1), 300) if rate_limited else backoff * (2 ** attempt)
            print(f"  fetch failed ({error}); retrying in {wait:.0f}s", file=sys.stderr)
            time.sleep(wait)
    raise RuntimeError(f"Fetch failed after {attempts} attempts: {url}") from last_error


def fetch_range(city: dict[str, Any], start: dt.date, end: dt.date) -> tuple[list[dict[str, Any]], float | None]:
    params = {
        "latitude": city["latitude"],
        "longitude": city["longitude"],
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
        "daily": DAILY_VARIABLES,
        "timezone": "Asia/Kolkata",
        "models": MODEL,
        "cell_selection": "land",
    }
    payload = http_get_json(f"{API_URL}?{urllib.parse.urlencode(params)}")
    if "daily" not in payload:
        raise RuntimeError(f"No daily payload for {city['id']}: {str(payload)[:300]}")
    daily = payload["daily"]
    records = []
    for idx, date_str in enumerate(daily["time"]):
        records.append(
            {
                "date": date_str,
                "tmax": daily["temperature_2m_max"][idx],
                "tmin": daily["temperature_2m_min"][idx],
                "precip": daily["precipitation_sum"][idx],
                "snow": daily["snowfall_sum"][idx],
            }
        )
    return records, payload.get("elevation")


def cache_path(city_id: str) -> Path:
    return CACHE_DIR / f"{city_id}.csv.gz"


def read_cache(city_id: str) -> list[dict[str, Any]]:
    path = cache_path(city_id)
    if not path.exists():
        return []
    with gzip.open(path, "rt", encoding="utf-8") as handle:
        rows = []
        for row in csv.DictReader(handle):
            rows.append(
                {
                    "date": row["date"],
                    "tmax": float(row["tmax"]) if row["tmax"] else None,
                    "tmin": float(row["tmin"]) if row["tmin"] else None,
                    "precip": float(row["precip"]) if row["precip"] else None,
                    "snow": float(row["snow"]) if row["snow"] else None,
                }
            )
        return rows


def write_cache(city_id: str, records: list[dict[str, Any]]) -> None:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(["date", "tmax", "tmin", "precip", "snow"])
    for record in records:
        writer.writerow(
            [
                record["date"],
                "" if record["tmax"] is None else record["tmax"],
                "" if record["tmin"] is None else record["tmin"],
                "" if record["precip"] is None else record["precip"],
                "" if record["snow"] is None else record["snow"],
            ]
        )
    # mtime=0 keeps the gzip byte-stable so unchanged data does not churn git
    with open(cache_path(city_id), "wb") as raw:
        with gzip.GzipFile(fileobj=raw, mode="wb", mtime=0) as handle:
            handle.write(buffer.getvalue().encode("utf-8"))


def merge_records(old: list[dict[str, Any]], new: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_date = {record["date"]: record for record in old}
    for record in new:
        by_date[record["date"]] = record
    return [by_date[key] for key in sorted(by_date)]


def refresh_city_data(city: dict[str, Any], mode: str, recent_end: dt.date) -> tuple[list[dict[str, Any]], float | None]:
    cached = read_cache(city["id"])
    elevation = None
    cache_complete = (
        cached
        and cached[0]["date"] <= FULL_START.isoformat()
        and dt.date.fromisoformat(cached[-1]["date"]) >= recent_end - dt.timedelta(days=7)
    )
    if (mode == "backfill" and not cache_complete) or not cached:
        records: list[dict[str, Any]] = []
        chunk_start = FULL_START
        while chunk_start <= recent_end:
            chunk_end = min(dt.date(chunk_start.year + 21, 12, 31), recent_end)
            print(f"  {city['id']}: fetching {chunk_start} to {chunk_end}", file=sys.stderr)
            chunk, elevation = fetch_range(city, chunk_start, chunk_end)
            records = merge_records(records, chunk)
            chunk_start = dt.date(chunk_end.year + 1, 1, 1)
            time.sleep(6.0)
        write_cache(city["id"], records)
        return records, elevation
    last_cached = dt.date.fromisoformat(cached[-1]["date"])
    fetch_from = last_cached - dt.timedelta(days=7)
    if fetch_from <= recent_end:
        print(f"  {city['id']}: updating {fetch_from} to {recent_end}", file=sys.stderr)
        fresh, elevation = fetch_range(city, fetch_from, recent_end)
        cached = merge_records(cached, fresh)
        write_cache(city["id"], cached)
    return cached, elevation


# --------------------------------------------------------------------------
# Percentile machinery (midrank, leave-one-year-out inside the baseline)
# --------------------------------------------------------------------------

def day_of_year(date: dt.date) -> int:
    return int(date.strftime("%j"))


def circular_distance(a: int, b: int, length: int = 366) -> int:
    direct = abs(a - b)
    return min(direct, length - direct)


class SeasonalPools:
    """Per calendar-day baseline pools with O(log n) midrank lookups.

    For each day-of-year, pools hold all baseline values within the centred
    window. Sorted full pools plus per-year sorted sub-pools allow
    leave-one-year-out ranks by subtraction.
    """

    def __init__(self, baseline: list[dict[str, Any]], variable: str, half_window: int,
                 condition=None) -> None:
        raw: dict[int, list[tuple[int, float]]] = {doy: [] for doy in range(1, 367)}
        for record in baseline:
            value = record[variable]
            if value is None or (condition is not None and not condition(record)):
                continue
            date = dt.date.fromisoformat(record["date"])
            raw[day_of_year(date)].append((date.year, float(value)))
        # expand into windows once, instead of per-target scanning
        self.full: dict[int, list[float]] = {}
        self.by_year: dict[int, dict[int, list[float]]] = {}
        for target in range(1, 367):
            members: list[tuple[int, float]] = []
            for doy in range(1, 367):
                if circular_distance(doy, target) <= half_window:
                    members.extend(raw[doy])
            self.full[target] = sorted(value for _, value in members)
            per_year: dict[int, list[float]] = {}
            for year, value in members:
                per_year.setdefault(year, []).append(value)
            self.by_year[target] = {year: sorted(values) for year, values in per_year.items()}

    def pool_size(self, doy: int, exclude_year: int | None) -> int:
        total = len(self.full[doy])
        if exclude_year is not None:
            total -= len(self.by_year[doy].get(exclude_year, []))
        return total

    def rank(self, doy: int, value: float, exclude_year: int | None) -> tuple[int, int, int] | None:
        """Return (#below, #equal, pool_size) with optional year excluded."""
        pool = self.full[doy]
        if not pool:
            return None
        below = bisect.bisect_left(pool, value)
        equal = bisect.bisect_right(pool, value) - below
        size = len(pool)
        if exclude_year is not None:
            sub = self.by_year[doy].get(exclude_year, [])
            if sub:
                below -= bisect.bisect_left(sub, value)
                equal -= bisect.bisect_right(sub, value) - bisect.bisect_left(sub, value)
                size -= len(sub)
        if size <= 0:
            return None
        return below, equal, size

    def midrank_pct(self, doy: int, value: float, exclude_year: int | None) -> float | None:
        ranked = self.rank(doy, value, exclude_year)
        if ranked is None:
            return None
        below, equal, size = ranked
        return (below + 0.5 * equal) / size

    def mean(self, doy: int) -> float | None:
        pool = self.full[doy]
        return statistics.fmean(pool) if pool else None

    def quantile(self, doy: int, q: float) -> float | None:
        pool = self.full[doy]
        if not pool:
            return None
        position = (len(pool) - 1) * q
        lower = math.floor(position)
        upper = math.ceil(position)
        if lower == upper:
            return pool[lower]
        weight = position - lower
        return pool[lower] * (1 - weight) + pool[upper] * weight


def exceed_statement(below: int, equal: int, size: int) -> dict[str, int]:
    """How many comparable baseline days were at or above this value."""
    return {"n_baseline": size, "n_at_or_above": size - below}


# --------------------------------------------------------------------------
# Classification
# --------------------------------------------------------------------------

def build_city_dataset(
    city: dict[str, Any],
    records: list[dict[str, Any]],
    api_elevation: float | None,
    recent_end: dt.date,
    validation: dict[str, Any],
    obs: dict[str, Any] | None = None,
    station: dict[str, Any] | None = None,
    imd_rain: dict[str, float] | None = None,
) -> dict[str, Any]:
    obs = obs or {}
    imd_rain = imd_rain or {}
    baseline = [
        record for record in records
        if BASE_START.isoformat() <= record["date"] <= BASE_END.isoformat()
    ]
    if len(baseline) < 9000:
        raise RuntimeError(f"{city['id']}: baseline too small ({len(baseline)} days)")

    tmax_pools = SeasonalPools(baseline, "tmax", HALF_WINDOW_DAYS)
    tmin_pools = SeasonalPools(baseline, "tmin", HALF_WINDOW_DAYS)
    wet_pools = SeasonalPools(
        baseline, "precip", HALF_WINDOW_DAYS,
        condition=lambda record: (record["precip"] or 0.0) >= WET_DAY_MM,
    )
    snow_pools = SeasonalPools(
        baseline, "snow", HALF_WINDOW_DAYS,
        condition=lambda record: (record["snow"] or 0.0) >= SNOW_DAY_CM,
    )
    # occurrence pools: every baseline day contributes 0/1 for "snow day"
    snow_occurrence = SeasonalPools(
        [
            {"date": record["date"], "occ": 1.0 if (record["snow"] or 0.0) >= SNOW_DAY_CM else 0.0}
            for record in baseline
        ],
        "occ",
        HALF_WINDOW_DAYS,
    )
    baseline_snow_days = sum(1 for record in baseline if (record["snow"] or 0.0) >= SNOW_DAY_CM)
    snow_capable = baseline_snow_days >= SNOW_CAPABLE_MIN_BASE_DAYS

    # annual ETCCDI R95p threshold: 95th percentile of ALL baseline wet days
    all_wet = sorted(
        float(record["precip"]) for record in baseline
        if record["precip"] is not None and record["precip"] >= WET_DAY_MM
    )
    r95p_threshold = None
    if len(all_wet) >= 100:
        position = (len(all_wet) - 1) * 0.95
        lower = math.floor(position)
        upper = math.ceil(position)
        weight = position - lower
        r95p_threshold = (
            all_wet[lower] if lower == upper
            else all_wet[lower] * (1 - weight) + all_wet[upper] * weight
        )

    classified: list[dict[str, Any]] = []
    for record in records:
        if record["date"] > recent_end.isoformat():
            continue
        date = dt.date.fromisoformat(record["date"])
        doy = day_of_year(date)
        in_base = BASE_START <= date <= BASE_END
        exclude = date.year if in_base else None

        day: dict[str, Any] = {
            "date": record["date"],
            "tmax": record["tmax"],
            "tmin": record["tmin"],
            "precip": record["precip"],
            "snow": record["snow"],
            "flags": [],
            "detail": {},
        }

        if record["tmax"] is not None:
            pct = tmax_pools.midrank_pct(doy, float(record["tmax"]), exclude)
            base_mean = tmax_pools.mean(doy)
            day["tmax_pct"] = pct
            day["tmax_anom"] = (
                round(float(record["tmax"]) - base_mean, 1) if base_mean is not None else None
            )
            if pct is not None:
                # daily MAX: high = warm day (TX90p), low = cold day (TX10p)
                if pct > HOT_PCT:
                    day["flags"].append("hot_extreme")
                elif pct > WARM_PCT:
                    day["flags"].append("warm_day")
                elif pct < COLD_EXTREME_PCT:
                    day["flags"].append("cold_day_extreme")
                elif pct < COLD_PCT:
                    day["flags"].append("cold_day")
                if pct > WARM_PCT:
                    ranked = tmax_pools.rank(doy, float(record["tmax"]), exclude)
                    if ranked:
                        day["detail"]["tmax"] = exceed_statement(*ranked)
                elif pct < COLD_PCT:
                    ranked = tmax_pools.rank(doy, float(record["tmax"]), exclude)
                    if ranked:
                        below, equal, size = ranked
                        day["detail"]["tmax_low"] = {"n_baseline": size, "n_at_or_below": below + equal}

        if record["tmin"] is not None:
            pct = tmin_pools.midrank_pct(doy, float(record["tmin"]), exclude)
            day["tmin_pct"] = pct
            if pct is not None:
                # daily MIN: low = cold night (TN10p), high = warm night (TN90p)
                if pct < COLD_EXTREME_PCT:
                    day["flags"].append("cold_extreme")
                elif pct < COLD_PCT:
                    day["flags"].append("cold_night")
                elif pct > HOT_PCT:
                    day["flags"].append("warm_night_extreme")
                elif pct > WARM_PCT:
                    day["flags"].append("warm_night")
                if pct < COLD_PCT:
                    ranked = tmin_pools.rank(doy, float(record["tmin"]), exclude)
                    if ranked:
                        below, equal, size = ranked
                        day["detail"]["tmin"] = {"n_baseline": size, "n_at_or_below": below + equal}
                elif pct > WARM_PCT:
                    ranked = tmin_pools.rank(doy, float(record["tmin"]), exclude)
                    if ranked:
                        day["detail"]["tmin_high"] = exceed_statement(*ranked)

        precip = float(record["precip"] or 0.0)
        if precip >= WET_DAY_MM:
            pool_size = wet_pools.pool_size(doy, exclude)
            if pool_size >= MIN_POOL_WET:
                pct = wet_pools.midrank_pct(doy, precip, exclude)
                day["precip_wet_pct"] = pct
                if pct is not None and pct > HEAVY_PRECIP_PCT:
                    day["flags"].append("heavy_precip")
                    ranked = wet_pools.rank(doy, precip, exclude)
                    if ranked:
                        day["detail"]["precip"] = exceed_statement(*ranked)
            if r95p_threshold is not None and precip > r95p_threshold:
                day["r95p_exceed"] = True

        if snow_capable:
            snow = float(record["snow"] or 0.0)
            if snow >= SNOW_DAY_CM:
                occ_ranked = snow_occurrence.rank(doy, 1.0, exclude)
                occ_prob = None
                if occ_ranked:
                    below, equal, size = occ_ranked
                    # pool values are 0/1; probability of a snow day is mean
                    ones = equal  # values equal to 1.0
                    occ_prob = ones / size if size else None
                    # ones counted via equal only works because value==1.0; zeros are 'below'
                day["snow_occ_prob"] = occ_prob
                if occ_prob is not None and occ_prob <= RARE_SNOW_MAX_PROB:
                    day["flags"].append("rare_snow")
                    day["detail"]["snow_occurrence"] = {
                        "baseline_days": size,
                        "baseline_snow_days": ones,
                    }
                pool_size = snow_pools.pool_size(doy, exclude)
                if pool_size >= MIN_POOL_SNOW:
                    pct = snow_pools.midrank_pct(doy, snow, exclude)
                    day["snow_amount_pct"] = pct
                    if pct is not None and pct > EXCEPTIONAL_SNOW_PCT:
                        day["flags"].append("exceptional_snow")
                        ranked = snow_pools.rank(doy, snow, exclude)
                        if ranked:
                            day["detail"]["snow_amount"] = exceed_statement(*ranked)

        classified.append(day)

    annual = summarize_annual(classified, snow_capable)
    ribbon = build_ribbon(tmax_pools, tmin_pools, wet_pools, snow_occurrence, snow_capable)
    events = top_events(classified, recent_end, city["id"], validation)
    observed_summary = attach_observed(events, obs, station)
    observed_summary["all_days"] = obs_all_day_agreement(records, obs) if station else {"days": 0, "agree": 0}
    imd_summary = attach_imd_rain(events, imd_rain)
    imd_summary["all_flags"] = imd_all_flag_agreement(classified, imd_rain)
    last_365 = [compact_day(day) for day in classified if day["date"] > (recent_end - dt.timedelta(days=365)).isoformat()]
    kpis = build_kpis(classified, recent_end, snow_capable)

    return {
        "schema_version": SCHEMA_VERSION,
        "meta": {
            "id": city["id"],
            "name": city["name"],
            "state": city["state"],
            "region": city["region"],
            "latitude": city["latitude"],
            "longitude": city["longitude"],
            "town_elevation_m": city.get("elevation_m"),
            "grid_cell_elevation_m": api_elevation,
            "snow_capable": snow_capable,
            "baseline": {"start": BASE_START.isoformat(), "end": BASE_END.isoformat()},
            "record_start": FULL_START.isoformat(),
            "record_end": recent_end.isoformat(),
            "half_window_days": HALF_WINDOW_DAYS,
            "wet_day_mm": WET_DAY_MM,
            "snow_day_cm": SNOW_DAY_CM,
            "r95p_threshold_mm": round(r95p_threshold, 1) if r95p_threshold else None,
        },
        "null_expectations": {
            "warm_day_fraction": 0.10,
            "hot_extreme_fraction": 0.05,
            "cold_night_fraction": 0.10,
            "cold_extreme_fraction": 0.05,
            "heavy_precip_wet_day_fraction": 0.05,
            "note": "Expected exceedance fractions if the climate matched the 1991-2020 baseline. Counts above these are the anomaly signal; counts near them are the base rate.",
        },
        "kpis": kpis,
        "observed": observed_summary,
        "imd_rain": imd_summary,
        "annual": annual,
        "ribbon": ribbon,
        "last_365": last_365,
        "events": events,
    }


def compact_day(day: dict[str, Any]) -> dict[str, Any]:
    compact = {
        "d": day["date"],
        "tx": round_or_none(day["tmax"], 1),
        "tn": round_or_none(day["tmin"], 1),
        "pr": round_or_none(day["precip"], 1),
        "sn": round_or_none(day["snow"], 1),
        "ta": day.get("tmax_anom"),
    }
    if day["flags"]:
        compact["f"] = day["flags"]
    if day.get("tmax_pct") is not None:
        compact["txp"] = round(day["tmax_pct"], 3)
    if day.get("tmin_pct") is not None:
        compact["tnp"] = round(day["tmin_pct"], 3)
    return compact


def round_or_none(value: Any, digits: int) -> float | None:
    return None if value is None else round(float(value), digits)


def summarize_annual(classified: list[dict[str, Any]], snow_capable: bool) -> list[dict[str, Any]]:
    buckets: dict[str, list[dict[str, Any]]] = {}
    for day in classified:
        buckets.setdefault(day["date"][:4], []).append(day)
    annual = []
    for year in sorted(buckets):
        days = buckets[year]
        n = len(days)
        temp_days = sum(1 for day in days if day.get("tmax_pct") is not None)
        wet_days = sum(1 for day in days if (day["precip"] or 0.0) >= WET_DAY_MM)
        entry = {
            "year": int(year),
            "coverage_days": n,
            "warm_days": sum(1 for day in days if "warm_day" in day["flags"] or "hot_extreme" in day["flags"]),
            "hot_extreme_days": sum(1 for day in days if "hot_extreme" in day["flags"]),
            "cold_nights": sum(1 for day in days if "cold_night" in day["flags"] or "cold_extreme" in day["flags"]),
            "cold_extreme_days": sum(1 for day in days if "cold_extreme" in day["flags"]),
            "heavy_precip_days": sum(1 for day in days if "heavy_precip" in day["flags"]),
            "r95p_days": sum(1 for day in days if day.get("r95p_exceed")),
            "wet_days": wet_days,
            "total_precip_mm": round(sum(day["precip"] or 0.0 for day in days), 0),
            "warm_day_fraction": round(
                sum(1 for day in days if "warm_day" in day["flags"] or "hot_extreme" in day["flags"]) / temp_days, 4
            ) if temp_days else None,
            "cold_night_fraction": round(
                sum(1 for day in days if "cold_night" in day["flags"] or "cold_extreme" in day["flags"]) / temp_days, 4
            ) if temp_days else None,
            "mean_tmax_anom": round(
                statistics.fmean([day["tmax_anom"] for day in days if day.get("tmax_anom") is not None]), 2
            ) if any(day.get("tmax_anom") is not None for day in days) else None,
        }
        if snow_capable:
            entry["rare_snow_days"] = sum(1 for day in days if "rare_snow" in day["flags"])
            entry["exceptional_snow_days"] = sum(1 for day in days if "exceptional_snow" in day["flags"])
            entry["snow_days"] = sum(1 for day in days if (day["snow"] or 0.0) >= SNOW_DAY_CM)
            entry["total_snow_cm"] = round(sum(day["snow"] or 0.0 for day in days), 0)
        annual.append(entry)
    return annual


def build_ribbon(tmax_pools, tmin_pools, wet_pools, snow_occurrence, snow_capable) -> dict[str, Any]:
    ribbon: dict[str, Any] = {
        "doy": list(range(1, 367)),
        "tmax_p05": [], "tmax_p50": [], "tmax_p95": [],
        "tmin_p05": [], "tmin_p50": [], "tmin_p95": [],
        "wet_p95": [],
    }
    if snow_capable:
        ribbon["snow_occ_prob"] = []
    for doy in range(1, 367):
        for key, pools, q in (
            ("tmax_p05", tmax_pools, 0.05), ("tmax_p50", tmax_pools, 0.50), ("tmax_p95", tmax_pools, 0.95),
            ("tmin_p05", tmin_pools, 0.05), ("tmin_p50", tmin_pools, 0.50), ("tmin_p95", tmin_pools, 0.95),
        ):
            value = pools.quantile(doy, q)
            ribbon[key].append(round_or_none(value, 1))
        wet95 = wet_pools.quantile(doy, 0.95) if wet_pools.full[doy] and len(wet_pools.full[doy]) >= MIN_POOL_WET else None
        ribbon["wet_p95"].append(round_or_none(wet95, 1))
        if snow_capable:
            occ = snow_occurrence.mean(doy)
            ribbon["snow_occ_prob"].append(round_or_none(occ, 3))
    return ribbon


FLAG_LABELS = {
    "hot_extreme": "Unusually warm day",
    "warm_day": "Warm day",
    "cold_extreme": "Unusually cold night",
    "cold_night": "Cold night",
    "warm_night_extreme": "Unusually warm night",
    "warm_night": "Warm night",
    "cold_day_extreme": "Unusually cold day",
    "cold_day": "Cold day",
    "heavy_precip": "Unusually wet",
    "rare_snow": "Rare-season snow",
    "exceptional_snow": "Exceptional snowfall",
}


def top_events(classified, recent_end: dt.date, city_id: str, validation) -> list[dict[str, Any]]:
    cutoff = (recent_end - dt.timedelta(days=EVENT_YEARS * 365)).isoformat()
    recent = [day for day in classified if day["date"] >= cutoff and day["flags"]]
    events = []
    for category, pct_key, value_key, unit in (
        ("hot_extreme", "tmax_pct", "tmax", "°C"),
        ("cold_extreme", "tmin_pct", "tmin", "°C"),
        ("warm_night_extreme", "tmin_pct", "tmin", "°C"),
        ("cold_day_extreme", "tmax_pct", "tmax", "°C"),
        ("heavy_precip", "precip_wet_pct", "precip", "mm"),
        ("rare_snow", "snow_occ_prob", "snow", "cm"),
        ("exceptional_snow", "snow_amount_pct", "snow", "cm"),
    ):
        candidates = [day for day in recent if category in day["flags"]]
        if category == "rare_snow":
            candidates.sort(key=lambda day: ((day.get("snow_occ_prob") or 1.0), -(day["snow"] or 0.0)))
        elif category in ("cold_extreme", "cold_day_extreme"):
            candidates.sort(key=lambda day: (day.get(pct_key) if day.get(pct_key) is not None else 1.0))
        else:
            candidates.sort(key=lambda day: -(day.get(pct_key) or 0.0))
        for day in candidates[:TOP_EVENTS_PER_CATEGORY]:
            event_id = f"{city_id}:{day['date']}:{category}"
            events.append(
                {
                    "event_id": event_id,
                    "date": day["date"],
                    "category": category,
                    "label": FLAG_LABELS[category],
                    "value": round_or_none(day.get(value_key), 1),
                    "unit": unit,
                    "tmax_pct": round_or_none(day.get("tmax_pct"), 3),
                    "tmin_pct": round_or_none(day.get("tmin_pct"), 3),
                    "precip_wet_pct": round_or_none(day.get("precip_wet_pct"), 3),
                    "snow_occ_prob": round_or_none(day.get("snow_occ_prob"), 3),
                    "snow_amount_pct": round_or_none(day.get("snow_amount_pct"), 3),
                    "detail": day["detail"],
                    "validation": validation.get(event_id, {"status": "not_checked"}),
                }
            )
    # dedupe date+category, keep strongest ordering stable
    seen = set()
    deduped = []
    for event in events:
        key = (event["date"], event["category"])
        if key not in seen:
            seen.add(key)
            deduped.append(event)
    return deduped


def build_kpis(classified, recent_end: dt.date, snow_capable: bool) -> dict[str, Any]:
    cutoff = (recent_end - dt.timedelta(days=365)).isoformat()
    window = [day for day in classified if day["date"] > cutoff]
    n_temp = sum(1 for day in window if day.get("tmax_pct") is not None)
    wet_days = sum(1 for day in window if (day["precip"] or 0.0) >= WET_DAY_MM)
    kpis = {
        "window_days": len(window),
        "warm_days": sum(1 for day in window if "warm_day" in day["flags"] or "hot_extreme" in day["flags"]),
        "warm_days_expected": round(0.10 * n_temp) if n_temp else None,
        "hot_extreme_days": sum(1 for day in window if "hot_extreme" in day["flags"]),
        "hot_extreme_days_expected": round(0.05 * n_temp) if n_temp else None,
        "cold_nights": sum(1 for day in window if "cold_night" in day["flags"] or "cold_extreme" in day["flags"]),
        "cold_nights_expected": round(0.10 * n_temp) if n_temp else None,
        # the inverse pair, completing the ETCCDI 2x2: warm nights (TN90p) and
        # cold days (TX10p). Warm nights are the dominant heat-health signal.
        "warm_nights": sum(1 for day in window if "warm_night" in day["flags"] or "warm_night_extreme" in day["flags"]),
        "warm_nights_expected": round(0.10 * n_temp) if n_temp else None,
        "cold_days": sum(1 for day in window if "cold_day" in day["flags"] or "cold_day_extreme" in day["flags"]),
        "cold_days_expected": round(0.10 * n_temp) if n_temp else None,
        "heavy_precip_days": sum(1 for day in window if "heavy_precip" in day["flags"]),
        "heavy_precip_days_expected": round(0.05 * wet_days) if wet_days else None,
        "wet_days": wet_days,
    }
    if snow_capable:
        kpis["rare_snow_days"] = sum(1 for day in window if "rare_snow" in day["flags"])
        kpis["exceptional_snow_days"] = sum(1 for day in window if "exceptional_snow" in day["flags"])
    return kpis


# --------------------------------------------------------------------------
# Main
# --------------------------------------------------------------------------

def load_validation() -> dict[str, Any]:
    if VALIDATION_FILE.exists():
        payload = json.loads(VALIDATION_FILE.read_text(encoding="utf-8"))
        return {entry["event_id"]: entry for entry in payload.get("events", [])}
    return {}


def load_stations() -> dict[str, Any]:
    if STATIONS_FILE.exists():
        return json.loads(STATIONS_FILE.read_text(encoding="utf-8"))
    return {}


def load_observed(city_id: str) -> dict[str, dict[str, float | None]]:
    """date -> {tmax, tmin, prcp} from the observed-station cache."""
    path = OBS_DIR / f"{city_id}.csv.gz"
    if not path.exists():
        return {}
    out: dict[str, dict[str, float | None]] = {}
    import gzip as _gz, csv as _csv
    with _gz.open(path, "rt", encoding="utf-8") as handle:
        for row in _csv.DictReader(handle):
            out[row["date"]] = {
                "tmax": float(row["tmax"]) if row["tmax"] else None,
                "tmin": float(row["tmin"]) if row["tmin"] else None,
                "prcp": float(row["prcp"]) if row["prcp"] else None,
            }
    return out


# which observed variable each flag is checked against
_OBS_VAR = {
    "hot_extreme": "tmax", "warm_day": "tmax",
    "cold_day_extreme": "tmax", "cold_day": "tmax",
    "cold_extreme": "tmin", "cold_night": "tmin",
    "warm_night_extreme": "tmin", "warm_night": "tmin",
    "heavy_precip": "prcp",
}


def attach_observed(events: list[dict[str, Any]], obs: dict, station: dict | None) -> dict[str, Any]:
    """Tag each event with the nearest station's observed value and whether
    ERA5 agrees. Returns a per-city summary of the temperature agreement rate."""
    checked = agree = 0
    for event in events:
        var = _OBS_VAR.get(event["category"])
        if station is None or var is None:
            event["observed"] = None  # snow, or no local station
            continue
        obs_day = obs.get(event["date"])
        obs_val = obs_day.get(var) if obs_day else None
        era5_val = event.get("value")
        if obs_val is None or era5_val is None:
            event["observed"] = {"station_km": station["distance_km"], "station": station["name"], "status": "no_obs"}
            continue
        entry = {
            "station_km": station["distance_km"],
            "station": station["name"],
            "var": var,
            "era5": era5_val,
            "obs": round(obs_val, 1),
        }
        if var == "prcp":
            # precip magnitude is noisy; check only that the station also saw rain
            entry["status"] = "agree" if obs_val >= OBS_WET_MM else "disagree"
        else:
            entry["delta"] = round(era5_val - obs_val, 1)
            hit = abs(era5_val - obs_val) <= OBS_TEMP_TOL_C
            entry["status"] = "agree" if hit else "disagree"
            checked += 1
            agree += 1 if hit else 0
        event["observed"] = entry
    return {
        "has_station": station is not None,
        "station": station["name"] if station else None,
        "station_km": station["distance_km"] if station else None,
        "temp_events_checked": checked,
        "temp_events_agree": agree,
    }


def load_imd_rain(city_id: str) -> dict[str, float]:
    """date -> IMD gridded rainfall (mm) for the city's 0.25 deg cell."""
    path = IMD_DIR / f"{city_id}.csv.gz"
    if not path.exists():
        return {}
    import gzip as _gz, csv as _csv
    out: dict[str, float] = {}
    with _gz.open(path, "rt", encoding="utf-8") as handle:
        for row in _csv.DictReader(handle):
            if row["rain_mm"] != "":
                out[row["date"]] = float(row["rain_mm"])
    return out


# how close IMD's gauge-based rainfall must be to ERA5's for a heavy-rain flag
# to count as corroborated: IMD also saw meaningful rain of comparable order.
IMD_MIN_MM = 1.0
IMD_RATIO = 0.33


def _imd_best(imd_rain: dict[str, float], date_iso: str) -> tuple[float | None, float | None]:
    """Best IMD rainfall within ±1 day of the date, and the exact-day value.
    The ±1-day window absorbs the day-boundary mismatch between IMD's
    0830-0830 IST accumulation and ERA5's daily aggregation — a storm can
    land on adjacent calendar dates in the two products."""
    exact = imd_rain.get(date_iso)
    base = dt.date.fromisoformat(date_iso)
    vals = [imd_rain.get((base + dt.timedelta(days=k)).isoformat()) for k in (-1, 0, 1)]
    vals = [v for v in vals if v is not None]
    return (max(vals) if vals else None), exact


def attach_imd_rain(events: list[dict[str, Any]], imd_rain: dict[str, float]) -> dict[str, Any]:
    """Cross-check heavy-precipitation flags against IMD gridded rainfall,
    with a ±1-day window for the accumulation-boundary mismatch."""
    checked = agree = 0
    for event in events:
        if event["category"] != "heavy_precip":
            event["imd"] = None
            continue
        best, _exact = _imd_best(imd_rain, event["date"])
        if best is None:  # e.g. an event after the IMD archive ends
            event["imd"] = {"status": "no_data"}
            continue
        era5 = event.get("value")
        hit = best >= IMD_MIN_MM and (era5 is None or best >= IMD_RATIO * era5)
        checked += 1
        agree += 1 if hit else 0
        event["imd"] = {"rain_mm": round(best, 1), "era5_mm": era5, "status": "agree" if hit else "disagree"}
    return {"precip_events_checked": checked, "precip_events_agree": agree}


def obs_all_day_agreement(records: list[dict[str, Any]], obs: dict) -> dict[str, int]:
    """ERA5-vs-station agreement across EVERY overlapping day, not just the
    strongest flagged events — an unbiased accuracy figure. Counts a day as a
    match when ERA5's max (and min) sit within the tolerance of the station's."""
    days = agree = 0
    for record in records:
        o = obs.get(record["date"])
        if not o:
            continue
        pairs = [(record["tmax"], o.get("tmax")), (record["tmin"], o.get("tmin"))]
        checkable = [(a, b) for a, b in pairs if a is not None and b is not None]
        if not checkable:
            continue
        days += 1
        if all(abs(float(a) - float(b)) <= OBS_TEMP_TOL_C for a, b in checkable):
            agree += 1
    return {"days": days, "agree": agree}


def imd_all_flag_agreement(classified: list[dict[str, Any]], imd_rain: dict[str, float]) -> dict[str, int]:
    """IMD confirmation across ALL ERA5 heavy-rain flags in the record (±1 day),
    not only the top events shown on cards."""
    flags = agree = 0
    for day in classified:
        if "heavy_precip" not in day["flags"]:
            continue
        best, _ = _imd_best(imd_rain, day["date"])
        if best is None:
            continue
        flags += 1
        era5 = day.get("precip") or 0.0
        if best >= IMD_MIN_MM and best >= IMD_RATIO * era5:
            agree += 1
    return {"flags": flags, "agree": agree}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=["backfill", "update"], default="update")
    parser.add_argument("--cities", default="", help="comma-separated city ids (default: all)")
    parser.add_argument("--recent-end", default="", help="override end date (default: today - 5 days)")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    cities = json.loads(CITIES_FILE.read_text(encoding="utf-8"))
    if args.cities:
        wanted = {city_id.strip() for city_id in args.cities.split(",")}
        cities = [city for city in cities if city["id"] in wanted]
        missing = wanted - {city["id"] for city in cities}
        if missing:
            raise SystemExit(f"Unknown city ids: {sorted(missing)}")
    recent_end = (
        dt.date.fromisoformat(args.recent_end)
        if args.recent_end
        else dt.date.today() - dt.timedelta(days=RECENT_LAG_DAYS)
    )
    validation = load_validation()
    stations = load_stations()

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUTPUT_DIR / "cities").mkdir(parents=True, exist_ok=True)

    index_entries = []
    failures = []
    for city in cities:
        print(f"Processing {city['name']}", file=sys.stderr)
        try:
            records, api_elevation = refresh_city_data(city, args.mode, recent_end)
            station = stations.get(city["id"])
            obs = load_observed(city["id"]) if station else {}
            imd_rain = load_imd_rain(city["id"])
            dataset = build_city_dataset(city, records, api_elevation, recent_end, validation, obs, station, imd_rain)
            out_path = OUTPUT_DIR / "cities" / f"{city['id']}.json"
            out_path.write_text(json.dumps(dataset, separators=(",", ":")) + "\n", encoding="utf-8")
            recent_days = dataset["last_365"]
            latest_flags = next(
                (day for day in reversed(recent_days) if day.get("f")), None
            )
            # national-leaderboard fields: flagged-day counts over short windows,
            # and the last-decade warm/cold exceedance rates for the "decade" view
            flags_7d = sum(1 for day in recent_days[-7:] if day.get("f"))
            flags_30d = sum(1 for day in recent_days[-30:] if day.get("f"))
            qual = [
                a for a in dataset["annual"]
                if a["coverage_days"] >= 350 and a.get("warm_day_fraction") is not None
            ]
            recent10 = qual[-10:]
            recent10_warm = (
                round(statistics.fmean([a["warm_day_fraction"] for a in recent10]), 4)
                if len(recent10) >= 8 else None
            )
            recent10_cold = (
                round(statistics.fmean([a["cold_night_fraction"] for a in recent10
                                        if a.get("cold_night_fraction") is not None]), 4)
                if len(recent10) >= 8 else None
            )
            index_entries.append(
                {
                    "id": city["id"],
                    "name": city["name"],
                    "state": city["state"],
                    "region": city["region"],
                    "latitude": city["latitude"],
                    "longitude": city["longitude"],
                    "snow_capable": dataset["meta"]["snow_capable"],
                    "kpis": dataset["kpis"],
                    "flags_7d": flags_7d,
                    "flags_30d": flags_30d,
                    "recent10_warm": recent10_warm,
                    "recent10_cold": recent10_cold,
                    "observed": dataset["observed"],
                    "imd_rain": dataset["imd_rain"],
                    "latest_flag": (
                        {"date": latest_flags["d"], "flags": latest_flags["f"]}
                        if latest_flags else None
                    ),
                }
            )
        except Exception as error:  # keep other cities alive on a single failure
            failures.append({"id": city["id"], "error": str(error)})
            print(f"  FAILED {city['id']}: {error}", file=sys.stderr)

    validation_summary = {"checked": 0, "validated": 0, "corroborated": 0, "unverified": 0, "contradicted": 0}
    for entry in validation.values():
        status = entry.get("status", "unverified")
        if status in validation_summary:
            validation_summary["checked"] += 1
            validation_summary[status] += 1

    # automatic observed cross-check: ERA5 vs nearest station, measured across
    # EVERY overlapping day (an unbiased accuracy figure), not just the flags
    observed_summary = {
        "cities_with_station": sum(1 for e in index_entries if e["observed"]["has_station"]),
        "cities_total": len(index_entries),
        "days_checked": sum(e["observed"]["all_days"]["days"] for e in index_entries),
        "days_agree": sum(e["observed"]["all_days"]["agree"] for e in index_entries),
        "tolerance_c": OBS_TEMP_TOL_C,
        "note": (
            "ERA5 daily max & min vs the nearest station (NOAA ISD/GHCN via Meteostat), across "
            "every overlapping day since 2010 — not only flagged events. Snow and the high "
            "Himalaya have no nearby station and stay single-source."
        ),
    }

    # independent rain cross-check against IMD gridded gauge data, across ALL
    # ERA5 heavy-rain flags (±1 day for the accumulation-boundary mismatch)
    imd_summary = {
        "cities_with_rain": sum(1 for e in index_entries if e["imd_rain"]["all_flags"]["flags"] > 0),
        "flags_checked": sum(e["imd_rain"]["all_flags"]["flags"] for e in index_entries),
        "flags_agree": sum(e["imd_rain"]["all_flags"]["agree"] for e in index_entries),
        "note": (
            "Every ERA5 heavy-rain flag cross-checked against IMD gridded daily rainfall (0.25 deg, "
            "Pai et al. 2014, gauge-based, independent of ERA5), within +/-1 day to absorb the "
            "0830-IST accumulation-boundary mismatch. Archive ends 2025, so 2026 flags aren't yet "
            "checkable; IMD has no snow or fine-resolution temperature product."
        ),
    }

    index = {
        "schema_version": SCHEMA_VERSION,
        "generated_at_utc": dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat(),
        "validation_summary": validation_summary,
        "observed_summary": observed_summary,
        "imd_rain_summary": imd_summary,
        "model": MODEL,
        "model_note": (
            "ERA5-Land (~9 km) blended with ERA5 (~31 km) via Open-Meteo era5_seamless; "
            "snowfall comes from ERA5 because ERA5-Land does not provide it. "
            "All values are reanalysis estimates, not station observations."
        ),
        "baseline": {"start": BASE_START.isoformat(), "end": BASE_END.isoformat()},
        "record_start": FULL_START.isoformat(),
        "recent_end": recent_end.isoformat(),
        "half_window_days": HALF_WINDOW_DAYS,
        "cities": index_entries,
        "failures": failures,
    }
    (OUTPUT_DIR / "index.json").write_text(json.dumps(index, separators=(",", ":")) + "\n", encoding="utf-8")
    print(f"Wrote {OUTPUT_DIR / 'index.json'} ({len(index_entries)} cities, {len(failures)} failures)", file=sys.stderr)
    return 1 if failures and not index_entries else 0


if __name__ == "__main__":
    raise SystemExit(main())

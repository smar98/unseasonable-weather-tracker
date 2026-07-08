#!/usr/bin/env python3
"""Build the dashboard dataset from public reanalysis data.

The first production source is Open-Meteo's historical API using ERA5. ERA5 is
chosen for the MVP because it provides a consistent multi-decade record and a
snowfall variable. For public interpretation, the app labels these values as
reanalysis estimates, not station observations.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import statistics
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any


API_URL = "https://archive-api.open-meteo.com/v1/archive"

DAILY_VARIABLES = ",".join(
    [
        "temperature_2m_max",
        "temperature_2m_min",
        "precipitation_sum",
        "snowfall_sum",
    ]
)

LOCATIONS: list[dict[str, Any]] = [
    {
        "id": "gulmarg",
        "name": "Gulmarg",
        "region": "Kashmir alpine belt",
        "state": "Jammu and Kashmir",
        "latitude": 34.0484,
        "longitude": 74.3805,
        "elevation_m": 2709,
        "terrain_note": "High-elevation resort town; surrounding peaks are materially higher than the point coordinate.",
    },
    {
        "id": "sonamarg",
        "name": "Sonamarg",
        "region": "Kashmir alpine belt",
        "state": "Jammu and Kashmir",
        "latitude": 34.3000,
        "longitude": 75.3000,
        "elevation_m": 2730,
        "terrain_note": "Alpine valley with nearby glacier terrain; point values should not be read as peak conditions.",
    },
    {
        "id": "srinagar",
        "name": "Srinagar",
        "region": "Kashmir valley",
        "state": "Jammu and Kashmir",
        "latitude": 34.0837,
        "longitude": 74.7973,
        "elevation_m": 1585,
        "terrain_note": "Valley location; useful contrast against higher Kashmir terrain.",
    },
    {
        "id": "leh",
        "name": "Leh",
        "region": "Ladakh cold desert",
        "state": "Ladakh",
        "latitude": 34.1526,
        "longitude": 77.5771,
        "elevation_m": 3500,
        "terrain_note": "High-altitude cold desert; precipitation is sparse and spatially variable.",
    },
    {
        "id": "manali",
        "name": "Manali",
        "region": "Himachal highlands",
        "state": "Himachal Pradesh",
        "latitude": 32.2432,
        "longitude": 77.1892,
        "elevation_m": 2050,
        "terrain_note": "Mountain town with strong nearby elevation gradients.",
    },
    {
        "id": "shimla",
        "name": "Shimla",
        "region": "Himachal mid-hills",
        "state": "Himachal Pradesh",
        "latitude": 31.1048,
        "longitude": 77.1734,
        "elevation_m": 2276,
        "terrain_note": "Urban hill-station point; useful for heat and rainfall anomaly monitoring.",
    },
    {
        "id": "joshimath",
        "name": "Joshimath",
        "region": "Uttarakhand highlands",
        "state": "Uttarakhand",
        "latitude": 30.5553,
        "longitude": 79.5656,
        "elevation_m": 1875,
        "terrain_note": "Gateway to higher Garhwal terrain; district-scale claims need elevation-band validation.",
    },
]


def parse_args() -> argparse.Namespace:
    today = dt.date.today()
    default_recent_end = today - dt.timedelta(days=5)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", default="public/data/anomaly_summary.json")
    parser.add_argument("--baseline-start", default="1991-01-01")
    parser.add_argument("--baseline-end", default="2020-12-31")
    parser.add_argument("--recent-start", default="2023-01-01")
    parser.add_argument("--recent-end", default=default_recent_end.isoformat())
    parser.add_argument("--model", default="era5")
    parser.add_argument("--window-days", type=int, default=7)
    parser.add_argument("--sleep-seconds", type=float, default=0.3)
    return parser.parse_args()


def fetch_daily(location: dict[str, Any], start: str, end: str, model: str) -> list[dict[str, Any]]:
    params = {
        "latitude": location["latitude"],
        "longitude": location["longitude"],
        "start_date": start,
        "end_date": end,
        "daily": DAILY_VARIABLES,
        "timezone": "Asia/Kolkata",
        "models": model,
        "cell_selection": "land",
    }
    url = f"{API_URL}?{urllib.parse.urlencode(params)}"
    with urllib.request.urlopen(url, timeout=90) as response:
        payload = json.load(response)

    if "daily" not in payload:
        raise RuntimeError(f"Open-Meteo response missing daily payload for {location['id']}: {payload}")

    daily = payload["daily"]
    records: list[dict[str, Any]] = []
    for idx, date_value in enumerate(daily["time"]):
        records.append(
            {
                "date": date_value,
                "date_obj": dt.date.fromisoformat(date_value),
                "tmax_c": daily["temperature_2m_max"][idx],
                "tmin_c": daily["temperature_2m_min"][idx],
                "precip_mm": daily["precipitation_sum"][idx],
                "snowfall_cm": daily["snowfall_sum"][idx],
            }
        )
    return records


def day_of_year(value: dt.date) -> int:
    return int(value.strftime("%j"))


def circular_day_distance(left: int, right: int, year_length: int = 366) -> int:
    direct = abs(left - right)
    return min(direct, year_length - direct)


def clean(values: list[float | int | None]) -> list[float]:
    return [float(value) for value in values if value is not None and not math.isnan(float(value))]


def quantile(values: list[float], q: float) -> float | None:
    values = sorted(values)
    if not values:
        return None
    if len(values) == 1:
        return values[0]
    position = (len(values) - 1) * q
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return values[lower]
    weight = position - lower
    return values[lower] * (1 - weight) + values[upper] * weight


def percentile_rank(values: list[float], observed: float | int | None) -> float | None:
    if observed is None:
        return None
    sample = clean(values)
    if not sample:
        return None
    observed_float = float(observed)
    return sum(1 for value in sample if value <= observed_float) / len(sample)


def window_values(records: list[dict[str, Any]], target_doy: int, variable: str, window_days: int) -> list[float]:
    values: list[float | int | None] = []
    for record in records:
        if circular_day_distance(day_of_year(record["date_obj"]), target_doy) <= window_days:
            values.append(record[variable])
    return clean(values)


def baseline_lookup(records: list[dict[str, Any]], target_date: dt.date, window_days: int) -> dict[str, Any]:
    target_doy = day_of_year(target_date)
    return baseline_stats_for_doy(records, target_doy, window_days)


def baseline_stats_for_doy(records: list[dict[str, Any]], target_doy: int, window_days: int) -> dict[str, Any]:
    tmax = window_values(records, target_doy, "tmax_c", window_days)
    tmin = window_values(records, target_doy, "tmin_c", window_days)
    precip = window_values(records, target_doy, "precip_mm", window_days)
    snow = window_values(records, target_doy, "snowfall_cm", window_days)
    snow_days = [1 if value > 0.1 else 0 for value in snow]
    return {
        "sample_size": len(tmax),
        "tmax_mean": statistics.fmean(tmax) if tmax else None,
        "tmax_p05": quantile(tmax, 0.05),
        "tmax_p50": quantile(tmax, 0.50),
        "tmax_p95": quantile(tmax, 0.95),
        "tmin_mean": statistics.fmean(tmin) if tmin else None,
        "tmin_p05": quantile(tmin, 0.05),
        "tmin_p50": quantile(tmin, 0.50),
        "tmin_p95": quantile(tmin, 0.95),
        "precip_p50": quantile(precip, 0.50),
        "precip_p95": quantile(precip, 0.95),
        "precip_p99": quantile(precip, 0.99),
        "snow_probability": statistics.fmean(snow_days) if snow_days else None,
        "tmax_percentile_values": tmax,
        "tmin_percentile_values": tmin,
        "precip_percentile_values": precip,
    }


def baseline_cache(records: list[dict[str, Any]], window_days: int) -> dict[int, dict[str, Any]]:
    return {doy: baseline_stats_for_doy(records, doy, window_days) for doy in range(1, 367)}


def classify_day(record: dict[str, Any], baseline: dict[str, Any]) -> dict[str, Any]:
    tmax_pct = percentile_rank(baseline["tmax_percentile_values"], record["tmax_c"])
    tmin_pct = percentile_rank(baseline["tmin_percentile_values"], record["tmin_c"])
    precip_pct = percentile_rank(baseline["precip_percentile_values"], record["precip_mm"])

    tmax_anomaly = None
    if record["tmax_c"] is not None and baseline["tmax_mean"] is not None:
        tmax_anomaly = float(record["tmax_c"]) - float(baseline["tmax_mean"])

    tmin_anomaly = None
    if record["tmin_c"] is not None and baseline["tmin_mean"] is not None:
        tmin_anomaly = float(record["tmin_c"]) - float(baseline["tmin_mean"])

    snow_probability = baseline["snow_probability"]
    snowfall = float(record["snowfall_cm"] or 0.0)
    precip = float(record["precip_mm"] or 0.0)

    heat_severity = max(0.0, ((tmax_pct or 0.0) - 0.90) / 0.10)
    cold_severity = max(0.0, (0.10 - (tmin_pct if tmin_pct is not None else 1.0)) / 0.10)
    rain_severity = max(0.0, ((precip_pct or 0.0) - 0.90) / 0.10) if precip >= 1.0 else 0.0
    snow_severity = 0.0
    if snowfall > 0.1 and snow_probability is not None and snow_probability <= 0.05:
        rarity_component = (0.05 - snow_probability) / 0.05
        amount_component = min(1.0, snowfall / 10.0)
        snow_severity = min(1.0, 0.7 * rarity_component + 0.3 * amount_component)

    scores = {
        "extreme_heat": heat_severity,
        "extreme_cold": cold_severity,
        "heavy_precipitation": rain_severity,
        "unseasonable_snow": snow_severity,
    }
    signal = max(scores, key=scores.get)
    score = min(1.0, scores[signal])
    if score < 0.35:
        signal = "within_seasonal_range"

    return {
        "date": record["date"],
        "tmax_c": round_or_none(record["tmax_c"], 1),
        "tmin_c": round_or_none(record["tmin_c"], 1),
        "precip_mm": round_or_none(record["precip_mm"], 1),
        "snowfall_cm": round_or_none(record["snowfall_cm"], 1),
        "tmax_anomaly_c": round_or_none(tmax_anomaly, 1),
        "tmin_anomaly_c": round_or_none(tmin_anomaly, 1),
        "tmax_percentile": round_or_none(tmax_pct, 3),
        "tmin_percentile": round_or_none(tmin_pct, 3),
        "precip_percentile": round_or_none(precip_pct, 3),
        "baseline_snow_probability": round_or_none(snow_probability, 3),
        "baseline_sample_size": baseline["sample_size"],
        "signal": signal,
        "anomaly_score": round(score, 3),
        "source_confidence": "reanalysis_estimate",
    }


def round_or_none(value: Any, digits: int) -> float | None:
    if value is None:
        return None
    return round(float(value), digits)


def month_key(value: dt.date) -> str:
    return value.strftime("%Y-%m")


def summarize_monthly(classified: list[dict[str, Any]]) -> list[dict[str, Any]]:
    buckets: dict[str, list[dict[str, Any]]] = {}
    for record in classified:
        buckets.setdefault(record["date"][:7], []).append(record)

    monthly: list[dict[str, Any]] = []
    for key in sorted(buckets):
        records = buckets[key]
        tmax_anoms = clean([record["tmax_anomaly_c"] for record in records])
        precip = clean([record["precip_mm"] for record in records])
        snow = clean([record["snowfall_cm"] for record in records])
        monthly.append(
            {
                "month": key,
                "mean_tmax_anomaly_c": round(statistics.fmean(tmax_anoms), 2) if tmax_anoms else None,
                "total_precip_mm": round(sum(precip), 1),
                "total_snowfall_cm": round(sum(snow), 1),
                "anomaly_days": sum(1 for record in records if record["signal"] != "within_seasonal_range"),
                "max_anomaly_score": round(max((record["anomaly_score"] for record in records), default=0.0), 3),
            }
        )
    return monthly[-36:]


def summarize_years(classified: list[dict[str, Any]]) -> list[dict[str, Any]]:
    buckets: dict[str, list[dict[str, Any]]] = {}
    for record in classified:
        buckets.setdefault(record["date"][:4], []).append(record)

    annual: list[dict[str, Any]] = []
    for year in sorted(buckets):
        records = buckets[year]
        may_sep_snow_estimate_days = 0
        for record in records:
            month = int(record["date"][5:7])
            if 5 <= month <= 9 and (record["snowfall_cm"] or 0) > 0.1:
                may_sep_snow_estimate_days += 1
        annual.append(
            {
                "year": year,
                "heat_days": sum(1 for record in records if record["signal"] == "extreme_heat"),
                "cold_days": sum(1 for record in records if record["signal"] == "extreme_cold"),
                "heavy_precip_days": sum(1 for record in records if record["signal"] == "heavy_precipitation"),
                "may_sep_snow_estimate_days": may_sep_snow_estimate_days,
                "flagged_unseasonable_snow_days": sum(
                    1 for record in records if record["signal"] == "unseasonable_snow"
                ),
                "anomaly_days": sum(1 for record in records if record["signal"] != "within_seasonal_range"),
            }
        )
    return annual


def location_summary(
    location: dict[str, Any],
    records: list[dict[str, Any]],
    baseline_start: dt.date,
    baseline_end: dt.date,
    recent_start: dt.date,
    recent_end: dt.date,
    window_days: int,
) -> dict[str, Any]:
    baseline_records = [record for record in records if baseline_start <= record["date_obj"] <= baseline_end]
    recent_records = [record for record in records if recent_start <= record["date_obj"] <= recent_end]
    if not baseline_records:
        raise RuntimeError(f"No baseline records for {location['id']}")
    if not recent_records:
        raise RuntimeError(f"No recent records for {location['id']}")

    cached_baselines = baseline_cache(baseline_records, window_days)
    classified = []
    for record in recent_records:
        baseline = cached_baselines[day_of_year(record["date_obj"])]
        classified.append(classify_day(record, baseline))

    top_events_by_signal: list[dict[str, Any]] = []
    for signal in ["extreme_heat", "extreme_cold", "heavy_precipitation", "unseasonable_snow"]:
        top_events_by_signal.extend(
            sorted(
                [record for record in classified if record["signal"] == signal],
                key=lambda item: item["anomaly_score"],
                reverse=True,
            )[:5]
        )
    deduped_top_events = {record["date"] + record["signal"]: record for record in top_events_by_signal}
    top_events = sorted(deduped_top_events.values(), key=lambda item: item["anomaly_score"], reverse=True)[:18]
    last_45_days = classified[-45:]
    monthly = summarize_monthly(classified)
    annual = summarize_years(classified)
    recent_365 = classified[-365:]

    return {
        "id": location["id"],
        "name": location["name"],
        "region": location["region"],
        "state": location["state"],
        "latitude": location["latitude"],
        "longitude": location["longitude"],
        "elevation_m": location["elevation_m"],
        "terrain_note": location["terrain_note"],
        "kpis": {
            "recent_anomaly_days_365": sum(1 for record in recent_365 if record["signal"] != "within_seasonal_range"),
            "recent_heavy_precip_days_365": sum(1 for record in recent_365 if record["signal"] == "heavy_precipitation"),
            "recent_heat_days_365": sum(1 for record in recent_365 if record["signal"] == "extreme_heat"),
            "recent_cold_days_365": sum(1 for record in recent_365 if record["signal"] == "extreme_cold"),
            "recent_unseasonable_snow_days_365": sum(
                1 for record in recent_365 if record["signal"] == "unseasonable_snow"
            ),
            "max_recent_score_365": round(max((record["anomaly_score"] for record in recent_365), default=0), 3),
            "july_august_snow_estimate_days_recent_period": sum(
                1
                for record in classified
                if record["date"][5:7] in {"07", "08"} and (record["snowfall_cm"] or 0) > 0.1
            ),
        },
        "last_45_days": last_45_days,
        "top_events": top_events,
        "monthly": monthly,
        "annual": annual,
    }


def main() -> int:
    args = parse_args()
    baseline_start = dt.date.fromisoformat(args.baseline_start)
    baseline_end = dt.date.fromisoformat(args.baseline_end)
    recent_start = dt.date.fromisoformat(args.recent_start)
    recent_end = dt.date.fromisoformat(args.recent_end)
    fetch_start = min(baseline_start, recent_start).isoformat()
    fetch_end = max(baseline_end, recent_end).isoformat()

    summaries = []
    for location in LOCATIONS:
        print(f"Fetching {location['name']} ({fetch_start} to {fetch_end})", file=sys.stderr)
        records = fetch_daily(location, fetch_start, fetch_end, args.model)
        summaries.append(
            location_summary(
                location,
                records,
                baseline_start,
                baseline_end,
                recent_start,
                recent_end,
                args.window_days,
            )
        )
        time.sleep(args.sleep_seconds)

    output = {
        "schema_version": "0.1.0",
        "generated_at_utc": dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat(),
        "analysis_window": {
            "baseline_start": args.baseline_start,
            "baseline_end": args.baseline_end,
            "recent_start": args.recent_start,
            "recent_end": args.recent_end,
            "day_of_year_window_days": args.window_days,
        },
        "source": {
            "provider": "Open-Meteo Historical Weather API",
            "provider_url": "https://open-meteo.com/en/docs/historical-weather-api",
            "model": args.model.upper(),
            "model_note": "ERA5 values are reanalysis estimates. They are suitable for screening anomalies but should be validated against station and satellite evidence before public event claims.",
        },
        "method_flags": [
            "Percentiles compare each day against the same day-of-year +/- the configured window in the 1991-2020 baseline.",
            "Snowfall is treated as an ERA5 reanalysis estimate, not direct observation.",
            "Mountain locations are point estimates; district or slope claims require elevation-band analysis.",
            "Climate attribution is intentionally out of scope for this MVP.",
        ],
        "locations": summaries,
    }

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(output, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {output_path}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

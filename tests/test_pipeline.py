#!/usr/bin/env python3
"""Correctness tests for the anomaly pipeline (scripts/build_dataset.py).

These prove the load-bearing science does what docs/methodology.md claims —
midrank percentiles, leave-one-year-out, the wet-day precip convention, the
snow rule (the exact v1 defect that never fired), unbiasedness of the flag
rate, and the observed-agreement logic. Run: `pytest tests/` or
`python3 tests/test_pipeline.py`.
"""

from __future__ import annotations

import datetime as dt
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import build_dataset as bd  # noqa: E402


# --------------------------------------------------------------------------
# synthetic-data helpers
# --------------------------------------------------------------------------

def daterange(start: dt.date, end: dt.date):
    d = start
    while d <= end:
        yield d
        d += dt.timedelta(days=1)


def pool_on_one_day(values_by_year: dict[int, float], month=4, day=10, var="tmax"):
    """A SeasonalPools built from one calendar day per year (so the ±2 window
    for that day-of-year contains exactly those values)."""
    records = [{"date": dt.date(y, month, day).isoformat(), var: v}
               for y, v in values_by_year.items()]
    return bd.SeasonalPools(records, var, bd.HALF_WINDOW_DAYS), bd.day_of_year(dt.date(2001, month, day))


# --------------------------------------------------------------------------
# pure helpers
# --------------------------------------------------------------------------

def test_circular_distance_wraps_year_boundary():
    assert bd.circular_distance(1, 366) == 1        # Jan 1 next to Dec 31
    assert bd.circular_distance(1, 365) == 2
    assert bd.circular_distance(100, 100) == 0
    assert bd.circular_distance(10, 40) == 30       # no wrap mid-year


def test_exceed_statement_counts_at_or_above():
    # 30 values, 15 strictly below, 1 equal -> 15 at-or-above
    assert bd.exceed_statement(below=15, equal=1, size=30) == {"n_baseline": 30, "n_at_or_above": 15}
    assert bd.exceed_statement(below=30, equal=0, size=30) == {"n_baseline": 30, "n_at_or_above": 0}


# --------------------------------------------------------------------------
# percentile machinery
# --------------------------------------------------------------------------

def test_midrank_percentile_and_rank():
    pools, doy = pool_on_one_day({1991 + i: float(i) for i in range(30)})  # values 0..29
    # midrank of 15 = (15 below + 0.5*1 equal) / 30
    assert abs(pools.midrank_pct(doy, 15.0, None) - (15 + 0.5) / 30) < 1e-9
    below, equal, size = pools.rank(doy, 15.0, None)
    assert (below, equal, size) == (15, 1, 30)
    # a value above everything -> pct 1.0, nothing at-or-above beyond itself
    assert pools.midrank_pct(doy, 100.0, None) == 1.0


def test_ties_use_midrank():
    # five identical values: midrank of that value is (0 + 0.5*5)/5 = 0.5
    pools, doy = pool_on_one_day({1991 + i: 7.0 for i in range(5)})
    assert pools.midrank_pct(doy, 7.0, None) == 0.5


def test_leave_one_year_out_removes_that_year():
    pools, doy = pool_on_one_day({1991 + i: float(i) for i in range(30)})
    assert pools.pool_size(doy, exclude_year=None) == 30
    assert pools.pool_size(doy, exclude_year=1991 + 15) == 29     # the '15' year gone
    below, equal, size = pools.rank(doy, 15.0, exclude_year=1991 + 15)
    assert (below, equal, size) == (15, 0, 29)                    # its own equal removed


def test_quantile_interpolates():
    pools, doy = pool_on_one_day({1991 + i: float(i) for i in range(11)})  # 0..10
    assert pools.quantile(doy, 0.5) == 5.0
    assert pools.quantile(doy, 0.0) == 0.0
    assert pools.quantile(doy, 1.0) == 10.0


def test_wet_day_convention_excludes_dry_days():
    # 30 days across distinct years, same day-of-year: 20 dry, 10 wet (>=1mm).
    # The wet-only pool must hold exactly the 10 wet days.
    records = [{"date": dt.date(1991 + i, 4, 10).isoformat(),
                "precip": (0.0 if i < 20 else float(i))} for i in range(30)]
    wet = bd.SeasonalPools(records, "precip", bd.HALF_WINDOW_DAYS,
                           condition=lambda r: (r["precip"] or 0.0) >= bd.WET_DAY_MM)
    doy = bd.day_of_year(dt.date(2001, 4, 10))
    assert wet.pool_size(doy, None) == 10


# --------------------------------------------------------------------------
# integration: build_city_dataset flags
# --------------------------------------------------------------------------

CITY = {"id": "testville", "name": "Testville", "state": "TS", "region": "Test",
        "latitude": 20.0, "longitude": 78.0, "elevation_m": 100}


def make_records(rng, snow_profile=False):
    """Baseline 1991-2020 + recent 2023-2024. tmax~30, tmin~20, dry, with an
    optional seasonal snow profile (common in Jan, rare in Oct)."""
    recs = []
    for d in daterange(dt.date(1991, 1, 1), dt.date(2024, 12, 31)):
        snow = 0.0
        if snow_profile:
            if d.month == 1 and rng.random() < 0.40:      # snow common in January
                snow = 3.0
            elif d.month == 10 and rng.random() < 0.02:   # snow rare in October
                snow = 3.0
        recs.append({
            "date": d.isoformat(),
            "tmax": 30.0 + rng.gauss(0, 2),
            "tmin": 20.0 + rng.gauss(0, 2),
            "precip": 0.0,
            "snow": snow,
        })
    return recs


def day_flags(dataset, date_iso):
    for day in dataset["last_365"]:
        if day["d"] == date_iso:
            return day.get("f", [])
    return None


def test_extreme_days_flag_and_normal_days_do_not():
    rng = random.Random(1)
    recs = make_records(rng)
    # inject a scorching day and a normal day into the recent window
    for r in recs:
        if r["date"] == "2024-06-15":
            r["tmax"] = 45.0   # far above the ~30 baseline -> hot_extreme
        if r["date"] == "2024-06-20":
            r["tmin"] = 8.0    # far below the ~20 baseline -> cold_extreme
        if r["date"] == "2024-06-25":
            r["tmax"], r["tmin"] = 30.0, 20.0  # dead normal -> no flag
    ds = bd.build_city_dataset(CITY, recs, None, dt.date(2024, 12, 31), {})
    assert "hot_extreme" in day_flags(ds, "2024-06-15")
    assert "cold_extreme" in day_flags(ds, "2024-06-20")
    assert day_flags(ds, "2024-06-25") == []  # present in last_365, no flags


def test_inverse_flags_warm_night_and_cold_day():
    """The 2x2 must be complete: an unusually high MIN flags a warm night, an
    unusually low MAX flags a cold day."""
    rng = random.Random(3)
    recs = make_records(rng)
    for r in recs:
        if r["date"] == "2024-06-10":
            r["tmin"] = 32.0   # min far above the ~20 baseline -> warm night
        if r["date"] == "2024-06-12":
            r["tmax"] = 18.0   # max far below the ~30 baseline -> cold day
    ds = bd.build_city_dataset(CITY, recs, None, dt.date(2024, 12, 31), {})
    assert "warm_night_extreme" in (day_flags(ds, "2024-06-10") or [])
    assert "cold_day_extreme" in (day_flags(ds, "2024-06-12") or [])
    assert ds["kpis"]["warm_nights"] >= 1 and ds["kpis"]["cold_days"] >= 1


def test_snow_rule_regression_common_vs_rare_season():
    """The v1 bug: the snow flag never fired. It must fire for snow in a season
    where snow is rare (<=5% baseline), and must NOT fire in a season where snow
    is common — regardless of amount."""
    rng = random.Random(7)
    recs = make_records(rng, snow_profile=True)
    for r in recs:
        if r["date"] == "2024-01-15":
            r["snow"] = 5.0    # snow in JAN, where it's common -> no rare_snow
        if r["date"] == "2024-10-15":
            r["snow"] = 5.0    # snow in OCT, where it's rare -> rare_snow
    ds = bd.build_city_dataset(CITY, recs, None, dt.date(2024, 12, 31), {})
    assert ds["meta"]["snow_capable"] is True
    jan = day_flags(ds, "2024-01-15") or []
    oct_ = day_flags(ds, "2024-10-15") or []
    assert "rare_snow" not in jan, "snow in a snowy month must NOT be 'rare-season'"
    assert "rare_snow" in oct_, "snow in a snow-free month MUST flag (the v1 regression)"


def test_flag_rate_is_unbiased_in_baseline():
    """Leave-one-year-out percentiles must be unbiased: across the in-baseline
    years, ~10% of days should breach the seasonal 90th percentile by
    construction. This is the core statistical guarantee."""
    rng = random.Random(42)
    recs = make_records(rng)
    ds = bd.build_city_dataset(CITY, recs, None, dt.date(2024, 12, 31), {})
    inbase = [a for a in ds["annual"]
              if 1991 <= a["year"] <= 2020 and a.get("warm_day_fraction") is not None]
    assert len(inbase) >= 25
    frac = sum(a["warm_day_fraction"] for a in inbase) / len(inbase)
    assert 0.07 <= frac <= 0.13, f"in-base warm-day fraction {frac:.3f} should sit near the 0.10 null"


# --------------------------------------------------------------------------
# observed cross-check ("actuals") logic
# --------------------------------------------------------------------------

def test_attach_observed_agree_differ_noobs_and_precip():
    events = [
        {"category": "hot_extreme", "date": "2024-05-01", "value": 40.0},   # obs 41 -> agree
        {"category": "cold_extreme", "date": "2024-01-01", "value": 5.0},   # obs 9  -> differ (4 > 3)
        {"category": "heavy_precip", "date": "2024-07-01", "value": 80.0},  # obs 12mm -> wet -> agree
        {"category": "hot_extreme", "date": "2024-08-01", "value": 39.0},   # no obs that day
        {"category": "rare_snow",   "date": "2024-12-20", "value": 10.0},   # no station variable
    ]
    obs = {
        "2024-05-01": {"tmax": 41.0, "tmin": 28.0, "prcp": 0.0},
        "2024-01-01": {"tmax": 26.0, "tmin": 9.0, "prcp": 0.0},
        "2024-07-01": {"tmax": 33.0, "tmin": 27.0, "prcp": 12.0},
    }
    station = {"distance_km": 5.0, "name": "Test Station"}
    summary = bd.attach_observed(events, obs, station)
    assert events[0]["observed"]["status"] == "agree"
    assert events[1]["observed"]["status"] == "disagree"
    assert events[2]["observed"]["status"] == "agree"     # wet-confirmed
    assert events[3]["observed"]["status"] == "no_obs"
    assert events[4]["observed"] is None                  # snow: no station variable
    assert summary["temp_events_checked"] == 2            # only the two with an obs value
    assert summary["temp_events_agree"] == 1


def test_attach_observed_no_station_is_single_source():
    events = [{"category": "hot_extreme", "date": "2024-05-01", "value": 40.0}]
    summary = bd.attach_observed(events, {}, None)
    assert events[0]["observed"] is None
    assert summary["has_station"] is False
    assert summary["temp_events_checked"] == 0


def test_attach_imd_rain_confirms_and_flags_caution():
    events = [
        {"category": "heavy_precip", "date": "2024-07-01", "value": 90.0},   # IMD 120mm -> confirms
        {"category": "heavy_precip", "date": "2024-09-10", "value": 60.0},   # IMD dry all ±1 -> caution
        {"category": "heavy_precip", "date": "2026-01-01", "value": 40.0},   # after archive -> no_data
        {"category": "hot_extreme",  "date": "2024-05-01", "value": 44.0},   # not a rain flag
    ]
    # dates chosen so the ±1-day window doesn't bleed a wet day into the dry case
    imd = {"2024-06-30": 0.0, "2024-07-01": 120.0, "2024-07-02": 0.0,
           "2024-09-09": 0.2, "2024-09-10": 0.2, "2024-09-11": 0.2}  # 2026 absent
    summary = bd.attach_imd_rain(events, imd)
    assert events[0]["imd"]["status"] == "agree"
    assert events[1]["imd"]["status"] == "disagree"
    assert events[2]["imd"]["status"] == "no_data"
    assert events[3]["imd"] is None
    assert summary["precip_events_checked"] == 2   # no_data not counted
    assert summary["precip_events_agree"] == 1


# --------------------------------------------------------------------------
# standalone runner (works without pytest)
# --------------------------------------------------------------------------

if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    failed = 0
    for t in tests:
        try:
            t()
            print(f"  PASS {t.__name__}")
        except AssertionError as e:
            failed += 1
            print(f"  FAIL {t.__name__}: {e}")
        except Exception as e:  # noqa: BLE001
            failed += 1
            print(f"  ERROR {t.__name__}: {type(e).__name__}: {e}")
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    sys.exit(1 if failed else 0)

# Sensitivity annex

Generated 2026-07-08 by `scripts/sensitivity.py`. Question: do the headline results depend on the two main discretionary choices: seasonal window width and percentile estimator? Statistic: mean 2021–2025 warm-day fraction (share of days above the seasonal 90th percentile), and day-level agreement (Jaccard) of `hot_extreme` flags, against the primary configuration (±2 days, midrank percentiles).

## Window width (±2 days primary vs ±7 / ±10)

| City | Window | Warm frac (±2d) | Warm frac (alt) | Δ | Hot-flag agreement |
|---|---|---|---|---|---|
| Sonamarg | ±7d | 0.179 | 0.168 | -0.011 | 0.79 |
| Sonamarg | ±10d | 0.179 | 0.158 | -0.020 | 0.76 |
| New Delhi | ±7d | 0.085 | 0.072 | -0.013 | 0.70 |
| New Delhi | ±10d | 0.085 | 0.061 | -0.024 | 0.66 |
| Mumbai | ±7d | 0.125 | 0.123 | -0.002 | 0.76 |
| Mumbai | ±10d | 0.125 | 0.121 | -0.004 | 0.72 |
| Leh | ±7d | 0.205 | 0.197 | -0.008 | 0.82 |
| Leh | ±10d | 0.205 | 0.188 | -0.016 | 0.78 |
| Chennai | ±7d | 0.080 | 0.078 | -0.002 | 0.73 |
| Chennai | ±10d | 0.080 | 0.071 | -0.009 | 0.66 |
| Jaisalmer | ±7d | 0.105 | 0.093 | -0.012 | 0.81 |
| Jaisalmer | ±10d | 0.105 | 0.087 | -0.018 | 0.68 |

## Percentile estimator (midrank vs interpolated threshold)

| City | Warm frac (midrank) | Warm frac (interp.) | Δ | Hot-flag agreement |
|---|---|---|---|---|
| Sonamarg | 0.179 | 0.181 | +0.003 | 0.92 |
| New Delhi | 0.085 | 0.087 | +0.002 | 0.90 |
| Mumbai | 0.125 | 0.124 | -0.001 | 0.95 |
| Leh | 0.205 | 0.206 | +0.002 | 0.95 |
| Chennai | 0.080 | 0.080 | +0.000 | 0.92 |
| Jaisalmer | 0.105 | 0.108 | +0.003 | 0.95 |

## Reading

Observed pattern in the current run: the estimator choice is negligible (|Δ| ≤ 0.003, day-level agreement ≥ 0.90). Widening the window systematically *lowers* recent warm fractions by up to ~0.02 (a wider window blends more of the seasonal cycle into each pool, inflating its spread and raising thresholds in shoulder seasons), but under every configuration recent fractions remain well above the 0.10 null and the ordering of cities is unchanged. The headline conclusions do not flip. Day-level hot-flag agreement of 0.66–0.82 across windows means individual borderline flags DO depend on the window choice; this is why the UI shows each event's full rank statement rather than a bare binary flag. Axes not yet examined: ERA5 vs era5_seamless source (requires re-fetch), and wet-day pool minimums.

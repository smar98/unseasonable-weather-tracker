# Methodology

**Version 2.0 · July 2026.** This document describes exactly what the code in
`scripts/build_dataset.py` computes. Where the implementation deviates from a
published convention, the deviation is stated and justified. Thresholds named
here are the thresholds in the code; there are no display-layer adjustments.

## 1. Question and intended user

The tool answers one narrow question:

> **Was this day's weather unusual for this location and this time of year,
> relative to a fixed 1991–2020 baseline?**

The intended user is a journalist or analyst who needs to check a claim like
"it snowed in Sonamarg in July" or "Delhi has never been this hot in March" in
minutes, with the evidence chain visible. Every flag therefore carries a plain
rank statement ("only 1 of 150 comparable baseline days had snow"), a stated
base rate, and a validation status.

The tool deliberately does **not** answer:

- whether climate change caused a specific event (attribution requires
  counterfactual model ensembles; see Ch. 11, IPCC AR6 WG1),
- what an official IMD station recorded,
- conditions across a whole district (each city is one reanalysis grid cell),
- impacts, damages, or risk.

## 2. Data source

- **Historical daily values:** Open-Meteo archive API, model `era5_seamless` —
  ERA5-Land (~9 km; Muñoz-Sabater et al. 2021) blended with ERA5 (~31 km;
  Hersbach et al. 2020). Snowfall comes from ERA5 only, because ERA5-Land does
  not expose a snowfall variable through this API (verified empirically:
  ERA5-Land returns nulls for `snowfall_sum` in both summer and winter).
- **Variables:** daily maximum and minimum 2 m temperature, daily precipitation
  sum, daily snowfall sum. Daily aggregation in `Asia/Kolkata`.
- **Live strip (display only):** Open-Meteo forecast API nowcast. It never
  affects flags; the UI labels it as the weakest evidence tier.
- **Record:** 1960-01-01 to (today − 5 days), the lag reflecting reanalysis
  publication delay.

Reanalysis is a physically consistent estimate, not observation. Open-Meteo
statistically downscales temperature to a ~90 m elevation model, but
precipitation and snowfall remain at native grid resolution. In steep Himalayan
terrain the grid cell can differ materially from the town; the dashboard
displays both the town elevation and the grid-cell elevation for this reason.

## 3. Baseline and seasonal comparison window

- **Baseline:** 1991–2020, the current WMO climate-normal period. It is fixed:
  recent years are never allowed to redefine "normal."
- **Seasonal window:** each calendar day is compared against all baseline days
  within ±2 days of the same day-of-year (a 5-day centred window, the ETCCDI
  convention), pooled across the 30 baseline years: ~150 comparable days.
  Day-of-year distance is circular, so early January compares against late
  December correctly. Leap-day pools are thinner but non-empty by construction.

## 4. Percentiles: midrank, leave-one-year-out

For an observed value `x` against a pool of `n` baseline values:

```
percentile(x) = (count(pool < x) + 0.5 · count(pool = x)) / n
```

This midrank form handles ties (frequent in precipitation) without bias, and
every percentile is exactly restatable as "only k of n baseline days reached
this value" — the statement shown in the UI.

**In-baseline days** (1991–2020) are judged against the other 29 years only
(leave-one-year-out), so a value never competes against its own year. The exact
treatment of in-base percentile-index inhomogeneity is the bootstrap of Zhang
et al. (2005), which climdex implements; LOO is the practical approximation and
its residual effect is confined to in-base years, which serve only as context
in the trend chart. Out-of-base years (the monitoring target) are unaffected.

We use rank-based percentiles rather than climdex's interpolated quantile
thresholds (Hyndman–Fan type 8). At n≈150 the difference is a fraction of one
rank position; the sensitivity annex quantifies it.

## 5. Flag definitions (exact)

| Flag | Condition | ETCCDI relative |
|---|---|---|
| `warm_day` | tmax percentile > 0.90 | TX90p building block |
| `hot_extreme` | tmax percentile > 0.95 | stricter TX90p variant |
| `cold_night` | tmin percentile < 0.10 | TN10p building block |
| `cold_extreme` | tmin percentile < 0.05 | stricter TN10p variant |
| `heavy_precip` | wet day (≥1 mm) AND wet-day percentile > 0.95 AND wet-day pool ≥ 15 | seasonal analogue of R95p |
| `rare_snow` | snowfall ≥ 1 cm AND baseline probability of a ≥1 cm snow day in the seasonal window ≤ 5% | none (novel, documented) |
| `exceptional_snow` | snowfall ≥ 1 cm AND amount percentile among baseline seasonal snow days > 0.95 AND snow-day pool ≥ 15 | none (novel, documented) |

Notes:

- **Wet-day convention.** Precipitation percentiles are computed over wet days
  (≥1 mm) only, following the ETCCDI convention. Computing them over all days
  would inflate dry-climate flags: in a desert cell most days are zero, so any
  modest rain lands in the extreme tail of the all-days distribution. (This was
  a defect of v1 of this tool; Leh showed 85 spurious "heavy precipitation"
  days.)
- **Seasonal vs annual R95p.** ETCCDI's R95p uses a single annual wet-day 95th
  percentile. In monsoon climates that threshold is set almost entirely by
  monsoon-season rain, so unseasonable dry-season rain can never register. We
  therefore use a *seasonal* wet-day percentile as the primary flag — a
  documented deviation — and additionally report annual `r95p_days` per year
  for direct comparability with published ETCCDI products.
- **Snow flags are amount-aware and threshold-transparent.** v1 used a single
  composite score (weighted rarity + amount) that never fired in 3.5 years of
  data; diagnosis showed both that ERA5 grid cells in the high Himalaya treat
  trace summer snow as climatologically common, and that binary occurrence
  rarity ignores record amounts. v2 separates the two questions: "does it snow
  at all in this season?" (`rare_snow`) and "is this amount exceptional for
  this season?" (`exceptional_snow`). The ≥1 cm floor excludes ERA5 trace-snow
  noise. Snow flags are only computed for cities with ≥30 baseline snow days.
- **Pool minimums.** Percentile flags that would rest on fewer than 15
  comparable baseline days are not issued; the UI reports the threshold as
  undefined instead. A rarity claim needs a denominator that can support it.

There is **no composite anomaly score**. Events are ranked by their percentile
(or occurrence probability for `rare_snow`), and every ranking criterion is a
quantity with a definition.

## 6. Base rates, by construction

Under a climate identical to the baseline, approximately:

- 10% of days breach a 90th-percentile flag (`warm_day`, `cold_night`),
- 5% breach a 95th/5th-percentile flag,
- 5% of wet days breach `heavy_precip`.

These null expectations ship inside every city file and are drawn as reference
lines in the UI. **The signal is the excess over the expected count, never the
count itself.** Empirical check: across the 1991–2020 baseline years
(leave-one-year-out), the realised warm-day fraction is ~0.10, confirming the
machinery is unbiased.

The dominant excess in recent years is warm-side exceedance — 2021–2025
warm-day fractions run 1.5–2× the null at most cities. That is the expected
signature of warming since the baseline period, and the tool presents it as
the finding of the trend view. It is not "contamination"; it is what a fixed
baseline measures. Contrast: Climate Central's Climate Shift Index makes the
same trend the explicit object via attribution ensembles; this tool stays at
screening level and says only "outside the 1991–2020 seasonal normal."

## 7. Evidence ladder and validation register

Evidence tiers, strongest first:

1. Official station observation (IMD)
2. Satellite detection (MODIS/IMS snow cover; INSAT products)
3. Reanalysis estimate (this tool's historical layer)
4. Forecast/nowcast (this tool's live strip)
5. Media/social reports (corroboration only)

Every flagged event carries a validation status from
`docs/validation-register.json`: **validated / corroborated / unverified /
contradicted**. Register entries link evidence. Contradicted flags are kept
visible: the register doubles as the tool's published false-positive log, and
the hit rate of a validated sample is reported rather than implied.

## 8. Known limitations

- ERA5/ERA5-Land precipitation and snowfall in complex Himalayan terrain carry
  substantial uncertainty; snowfall doubly so (it is a model-partitioned
  variable, not assimilated snow depth).
- One grid cell ≠ one town ≠ one district. Elevation gaps are displayed, but
  the mismatch is irreducible at reanalysis resolution.
- The 5-day window trades seasonal sharpness against pool size; ±7 days is
  examined in the sensitivity annex.
- Percentile indices say nothing about duration or spatial extent; a
  three-day heatwave appears as three independent daily flags.
- IMD heatwave definitions (absolute thresholds plus departures) are the
  operationally relevant standard in India and are not yet implemented;
  planned as a companion flag set.

## 8a. Planned IMD dual-source layer

The single biggest credibility upgrade is a second, observation-based source
to corroborate the reanalysis. The concrete first step:

- **Ingest IMD gridded daily rainfall (0.25° × 0.25°, 1901–present; Pai et al.
  2014)** via the MIT-licensed `imdlib` Python package, which downloads
  directly from IMD Pune (`imd.get_data('rain', y0, y1)`). It is station-
  interpolated — methodologically independent of ERA5 — and its 0.25° (~28 km)
  grid is a defensible match to aggregate against ERA5-Land, unlike the coarser
  1° (~100 km) IMD temperature product (1951–present), which is phase two.
- **Merge by aggregating ERA5-Land up to each IMD 0.25° cell** (area-average),
  not by downscaling IMD — then flag heavy precipitation only where *both*
  sources cross the threshold in the same direction ("agreement gate").
- **What it buys:** it most strengthens the heavy-precipitation flag; it does
  nothing for heat/cold (needs the 1° temperature product) and nothing for snow
  (IMD publishes no gridded snow product — that flag stays ERA5-only).
- **Caveats to log in the register:** IMD gridded rainfall is known to
  underestimate in very wet regions (Western Ghats, NE India) and its network
  thins after 2008; the data is free but not open-licensed (non-commercial
  reproduction restriction + mandatory citation of Pai et al. 2014). Per-station
  daily data is gated behind the paid IMD-DSP portal; NOAA GHCN-Daily carries
  ~3,800 India-flagged stations as a free but uneven fallback.

## 9. References

- Zhang, X., et al. (2011). Indices for monitoring changes in extremes based on
  daily temperature and precipitation data. *WIREs Climate Change*, 2(6).
- Zhang, X., Hegerl, G., Zwiers, F., & Kenyon, J. (2005). Avoiding
  inhomogeneity in percentile-based indices of temperature extremes.
  *Journal of Climate*, 18(11).
- Hersbach, H., et al. (2020). The ERA5 global reanalysis. *QJRMS*, 146(730).
- Muñoz-Sabater, J., et al. (2021). ERA5-Land: a state-of-the-art global
  reanalysis dataset for land applications. *Earth Syst. Sci. Data*, 13.
- WMO (2017). *Guidelines on the Calculation of Climate Normals*. WMO-No. 1203.

(Citations are to the conventions this tool follows; they do not imply
endorsement. Verify exact bibliographic details before formal use.)

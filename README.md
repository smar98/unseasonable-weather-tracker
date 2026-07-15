# Unseasonable — India seasonal anomaly tracker

**Live:** https://smar98.github.io/unseasonable-weather-tracker/

A public-data screening tool that answers one narrow question for 62 Indian
cities: **was this day's weather unusual for this place and this time of
year?** Every day since 1960 is compared against the same season in a fixed
1991–2020 baseline using percentile indices from the WMO ETCCDI family
(Zhang et al. 2011), computed on ERA5/ERA5-Land reanalysis.

Built for the practitioner who needs to check a claim like "it snowed in
Sonamarg in July" in minutes: every flag is a plain rank statement ("only 1 of
150 comparable baseline days had snow"), ships with its base rate, and carries
a validation status against independent evidence.

What it is **not**: an attribution model (it never says climate change caused
an event), a station record (all values are reanalysis estimates), or a
district-level product (each city is one grid cell).

## Method in one paragraph

Daily tmax/tmin/precipitation/snowfall from Open-Meteo's `era5_seamless`
(ERA5-Land ~9 km blended with ERA5 ~31 km; snowfall from ERA5). Each calendar
day is compared to a 5-day centred seasonal window pooled across 1991–2020
(~150 values), with leave-one-year-out for in-baseline days. Midrank
percentiles produce flags: warm/hot days (p90/p95), cold nights (p10/p05),
heavy precipitation (wet-day p95, wet days ≥1 mm only, per the ETCCDI
convention), and two amount-aware snow flags (rare-season occurrence;
exceptional seasonal amount). Null expectations (~10%/~5% by construction) are
displayed everywhere counts are. Full detail, deviations, and references:
[docs/methodology.md](docs/methodology.md).

## Repository layout

```
scripts/build_dataset.py    pipeline (backfill + daily incremental update)
scripts/cities.json         the 62 monitored cities
data_cache/<id>.csv.gz      raw daily reanalysis values, 1960–present
public/data/index.json      city index + latest flags (map/list payload)
public/data/cities/<id>.json per-city dataset (charts, events, KPIs)
docs/methodology.md         full methodology with citations
docs/source-register.md     evidence sources and tiers
docs/validation-register.json manual event-validation log (incl. misses)
index.html / app.js / styles.css  static dashboard, hand-built SVG charts
```

## Run locally

```bash
python3 scripts/build_dataset.py --mode update   # refresh recent days
python3 -m http.server 8000                      # then open localhost:8000
```

Full rebuild from scratch: `--mode backfill` (fetches 1960–present for all 62
cities; respects Open-Meteo rate limits, so expect a long run).

## Data refresh

`.github/workflows/update-data.yml` runs daily at 03:20 UTC: incremental fetch
of trailing days per city, reclassification, commit. Deployment is GitHub
Pages via `.github/workflows/pages.yml` on push to `main`.

## License and attribution

Code: MIT. Weather data: ERA5/ERA5-Land (Copernicus/ECMWF) via Open-Meteo
(CC-BY 4.0, non-commercial API tier). Basemap © OpenStreetMap contributors,
© CARTO. Cite reanalysis values as estimates, never as station observations.

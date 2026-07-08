# Seasonal Anomaly Tracker

A public-data dashboard for screening unseasonable weather in the Western Himalaya.

The project starts with a narrow, defensible question:

> How unusual was this weather condition for this location and time of year?

It does not claim that individual events were caused by climate change. The MVP uses ERA5 reanalysis through the Open-Meteo Historical Weather API to compute day-of-year percentiles against a 1991-2020 baseline.

## What It Tracks

- Extreme heat relative to the same time of year.
- Extreme cold relative to the same time of year.
- Heavy precipitation relative to the same time of year.
- Candidate unseasonable snowfall events.

## Current Scope

Initial locations:

- Gulmarg
- Sonamarg
- Srinagar
- Leh
- Manali
- Shimla
- Joshimath

The first scope is intentionally point-based. District or regional claims require elevation-band analysis and source validation.

## Run Locally

Generate the dataset:

```bash
python3 scripts/build_dataset.py
```

Serve the static dashboard:

```bash
python3 -m http.server 8000
```

Then open:

```text
http://localhost:8000
```

## Methodology

Read [docs/methodology.md](docs/methodology.md) before interpreting any result.

Key choices:

- Baseline: 1991-2020.
- Seasonal comparison: same day-of-year plus/minus 7 days.
- Source label: reanalysis estimate.
- Climate attribution: out of scope.

## Source Register

The current and planned sources are documented in [docs/source-register.md](docs/source-register.md).

## Limitations

- ERA5 is not station data.
- Mountain terrain has sharp elevation and aspect gradients.
- Snowfall and snow cover are different phenomena.
- The anomaly score is for screening and sorting only.
- Public event claims should be validated against station, satellite, or official evidence.

## Roadmap

1. Add MODIS/IMS snow-cover ingestion.
2. Aggregate snow-cover anomalies by elevation band.
3. Add IMD station validation where licensing/access permits.
4. Add uncertainty flags by source and terrain complexity.
5. Add district polygons after point metrics are validated.

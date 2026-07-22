# Source Register

This file records the intended source hierarchy and how each source should be used.

## Active MVP Source

### Open-Meteo Historical Weather API

- URL: https://open-meteo.com/en/docs/historical-weather-api
- Role: API access layer for ERA5/ERA5-Land/IFS data.
- Current use: model `era5_seamless`, which blends ERA5-Land (~9 km) with
  ERA5 (~31 km), for daily tmax/tmin/precipitation; snowfall falls back to
  ERA5 because ERA5-Land exposes no snowfall variable through this API
  (verified empirically, July 2026: `snowfall_sum` is null from `era5_land`
  in both winter and summer test windows).
- Strength: Easy reproducibility; no key required for non-commercial prototyping.
- Limitation: an API wrapper over the original data archive; temperature is
  statistically downscaled to a ~90 m elevation model but precipitation and
  snowfall are not; rate-limited (call-weighted), which the pipeline's
  backoff respects. Model output should be cited as reanalysis estimates.

### Open-Meteo Forecast API

- URL: https://open-meteo.com/en/docs
- Role: Live/near-live forecast and nowcast access layer.
- Current use: Browser-side current temperature, precipitation, snowfall, weather code, and wind speed.
- Strength: No API key required for prototyping; supports public static dashboard deployment.
- Limitation: a forecast/nowcast model estimate. It should not overwrite the historical anomaly layer.

### ERA5

- URL: https://cds.climate.copernicus.eu/datasets/reanalysis-era5-single-levels
- Reference: Hersbach et al. (2020), QJRMS.
- Role: Long-run global reanalysis.
- Current use: Underlying source for daily anomaly screening; sole source for
  snowfall.
- Strength: Hourly data from 1940 to present at global scale.
- Limitation: 0.25 degree resolution can be coarse for Himalayan valleys and
  ridges; snowfall is a model-partitioned variable and carries extra
  uncertainty in complex terrain.

### ERA5-Land

- URL: https://cds.climate.copernicus.eu/datasets/reanalysis-era5-land
- Reference: Muñoz-Sabater et al. (2021), ESSD.
- Role: Higher-resolution (~9 km) land-surface reanalysis, blended into
  `era5_seamless`.
- Current use: Improves temperature/precipitation resolution, especially in
  mountain terrain.
- Limitation: No snowfall variable via the Open-Meteo API; still far coarser
  than valley-scale variation.

## Active Validation Sources (independent of ERA5)

### NOAA ISD / GHCN-Daily station observations (via Meteostat)

- URL: https://dev.meteostat.net/ (wraps https://www.ncei.noaa.gov/ ISD + GHCN-Daily)
- Role: real observed daily station data (the temperature cross-check).
- Current use: nearest station ≤35 km for 41 of 62 cities; each recent
  heat/cold flag marked agree/differs vs ERA5 within 3 °C. ~88% agree.
- Strength: genuine point measurements, free (CC-BY), no key, Actions-runnable.
- Limitation: wind largely absent for India; hill/remote points (Leh,
  Sonamarg, Joshimath, Manali) have no station; precip at stations is spotty
  (so rain is checked via IMD instead).

### IMD gridded daily rainfall (0.25°, Pai et al. 2014, via imdlib)

- URL: https://www.imdpune.gov.in/cmpg/Griddata/Rainfall_25_NetCDF.html
- Role: India-official, gauge-based rainfall (the heavy-rain cross-check).
- Current use: each city's 0.25° cell, 2015–2025; every heavy_precip flag
  marked agree/disagree vs IMD. ~45% confirmed (reanalysis rain is poorly
  located at grid scale; stated as extra caution on rain flags).
- Strength: independent of ERA5 (gauge-interpolated), covers all India incl.
  no-station cities, free.
- Limitation: rainfall only; archive ends 2025; underestimates in very wet
  regions; not open-licensed (cite Pai et al. 2014, non-commercial restriction).

## Planned Validation Sources

### IMD public API (api.imd.gov.in): access requested July 2026

- Role: potential station-level official actuals (temperature, rain, wind).
- Intended use: if granted, supersedes the gridded rainfall layer for covered
  cities and could add an official temperature/wind check; it plugs into the same
  per-event agreement slot. Pending access result.

### IMD Data Service Portal

- URL: https://dsp.imdpune.gov.in/
- Role: Official Indian meteorological observations and climate data.
- Intended use: Station validation and official event confirmation.
- Limitation: Some detailed historical data may require registration, approval, or payment.

### IMD Climate Research & Services, Pune

- URL: https://imdpune.gov.in/
- Role: Official climate summaries, services, observed rainfall/temperature links, and hazard products.
- Intended use: Official context, climate summaries, and validation references.

### NASA MODIS/Terra Snow Cover Daily L3 Global 500 m

- URL: https://nsidc.org/data/mod10a1/versions/61
- Role: Satellite snow-cover validation.
- Intended use: Detect snow-cover presence and spatial extent.
- Limitation: Cloud cover, ephemeral/thin snow, and optical retrieval constraints.

### NOAA/NIC IMS Daily Northern Hemisphere Snow and Ice Analysis

- URL: https://nsidc.org/data/g02156/versions/1
- Role: Operational snow-cover analysis.
- Intended use: Independent snow-cover corroboration.
- Limitation: Operational product; not suitable as the only evidence layer for high-stakes decisions.

## Evidence Labels

The dashboard uses these labels:

- `station_observation`: official measured weather observation.
- `satellite_detection`: raster or imagery-derived snow/rain/cloud/land signal.
- `reanalysis_estimate`: physically consistent historical reconstruction.
- `forecast_or_nowcast`: near-real-time model output.
- `media_report`: journalistic or social corroboration.

The MVP currently emits `reanalysis_estimate`.

The live panel emits a separate `forecast_or_nowcast` interpretation label in the interface.

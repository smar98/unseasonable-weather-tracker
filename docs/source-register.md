# Source Register

This file records the intended source hierarchy and how each source should be used.

## Active MVP Source

### Open-Meteo Historical Weather API

- URL: https://open-meteo.com/en/docs/historical-weather-api
- Role: API access layer for ERA5/ERA5-Land/IFS data.
- Current use: ERA5 daily temperature, precipitation, and snowfall.
- Strength: Easy reproducibility; no key required for non-commercial prototyping.
- Limitation: API wrapper, not the original data archive; model output should be cited as reanalysis estimates.

### ERA5

- URL: https://cds.climate.copernicus.eu/datasets/reanalysis-era5-single-levels
- Role: Long-run global reanalysis.
- Current use: Underlying source for daily anomaly screening.
- Strength: Hourly data from 1940 to present at global scale.
- Limitation: 0.25 degree resolution can be coarse for Himalayan valleys and ridges.

## Planned Validation Sources

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

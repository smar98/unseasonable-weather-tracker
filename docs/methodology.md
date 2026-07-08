# Methodology

This project is an anomaly tracker, not an attribution model. It is designed to answer a narrow question:

> How unusual was this observed or estimated weather condition for this location and time of year?

It does not claim that a specific event was caused by climate change. Climate attribution requires a separate event-attribution design using counterfactual climate modeling, uncertainty intervals, and peer-reviewed methods.

## 1. Definitions

### Weather event

A dated condition at a location, such as high temperature, heavy rainfall, snowfall, or cold precipitation.

### Seasonal anomaly

A weather event that is unusual relative to the historical distribution for the same part of the year.

The MVP computes this using a day-of-year comparison window:

```text
Observed day D is compared with all baseline days from D - 7 to D + 7
across the baseline period.
```

This keeps the comparison seasonal. A July 8 event is not compared with January weather.

### Unseasonable snow

Snowfall is flagged as unseasonable when:

```text
snowfall_cm > 0.1
and baseline probability of snow for the same day-of-year window <= 5%
```

For public-facing claims, this should be corroborated with satellite snow-cover products, station observations, credible local reports, or direct imagery.

### Extreme heat

Daily maximum temperature is flagged when:

```text
temperature_2m_max percentile >= 95th percentile
```

The percentile is relative to the same day-of-year window in the baseline period.

### Extreme cold

Daily minimum temperature is flagged when:

```text
temperature_2m_min percentile <= 5th percentile
```

### Heavy precipitation

Daily precipitation is flagged when:

```text
precipitation_sum percentile >= 95th percentile
and precipitation_sum >= 1 mm
```

The 1 mm floor avoids labeling trace precipitation as extreme in extremely dry climates.

## 2. Baseline

The default baseline is:

```text
1991-01-01 through 2020-12-31
```

This follows the common 30-year climate-normal convention. The app uses a fixed historical baseline instead of moving recent averages so that recent years are not allowed to redefine the comparison set.

## 3. Current Data Source

The MVP uses the Open-Meteo Historical Weather API with ERA5 reanalysis.

ERA5 is a physically consistent global reanalysis. It combines model physics with assimilated observations to reconstruct historical weather. It is useful for screening anomalies over long periods, especially where station data access is hard.

Important limitation:

```text
ERA5 is not a station observation.
```

In steep mountain terrain, point estimates may differ materially from actual valley, slope, ridge, or peak conditions. A dashboard claim about "Kashmir" or "a district" should not be based on a single grid-point estimate.

## 4. Evidence Ladder

The dashboard should treat evidence in layers.

1. Official station observation, when available.
2. Satellite detection, especially for snow cover.
3. Reanalysis estimate, such as ERA5.
4. Forecast or nowcast model output.
5. Media or social report.

Strong public claims should use at least two independent layers where possible.

## 5. Snowfall vs Snow Cover

Snowfall and snow cover are different.

Snowfall means new snow fell during a period. Snow cover means snow is visible or present on the ground. In the Himalaya, snow cover can persist from previous storms, glaciers, shaded slopes, or old snow patches.

The current MVP measures ERA5 snowfall estimates. A stronger snow module should add:

- MODIS/Terra daily snow cover at 500 m resolution.
- NOAA/NIC IMS daily snow and ice analysis.
- Elevation-band aggregation using a digital elevation model.
- Cloud screening and confidence flags.

## 6. Mountain Terrain Caveat

Himalayan weather changes sharply by elevation and aspect. A credible version of this project must avoid statements like:

```text
It snowed in Kashmir.
```

unless the location, elevation, and evidence are specified.

Prefer:

```text
ERA5 estimated snowfall near [coordinate/elevation] on [date].
```

or:

```text
Satellite snow cover expanded above 3,500 m in the Kashmir alpine belt.
```

## 7. Anomaly Score

The dashboard includes an anomaly score for sorting. It is not a climate-risk score.

The score is a bounded 0-1 screening value derived from the strongest daily signal among heat, cold, precipitation, and unseasonable snow. It is used only to rank candidate events for review.

The score must not be described as:

- climate vulnerability
- climate impact
- disaster risk
- probability of harm
- attribution confidence

## 8. Validation Plan

Before making public claims, validate a sample of flagged events against:

- IMD station records or official summaries.
- MODIS/IMS snow-cover rasters for snow events.
- Local district/state disaster bulletins for heavy rainfall.
- News archives only as corroborating context, not primary data.

## 9. Expansion Plan

The next methodological upgrades are:

1. Add satellite snow-cover ingestion and elevation bands.
2. Add IMD station cross-checks where licensing/access allows.
3. Add uncertainty labels by source and terrain type.
4. Add sensitivity checks across ERA5 and ERA5-Land for temperature/precipitation.
5. Add district polygons only after point-level metrics are validated.

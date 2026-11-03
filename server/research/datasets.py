"""Dataset registry: the ids, kinds, units, sources, licences and exact
attribution texts the catalog serves and the assembler resolves.

Licence wording follows THIRD-PARTY.md (the repo's own ledger). Where the
ledger says a licence is unconfirmed, the registry says so; it is never
upgraded here.
"""
from __future__ import annotations

PORTWATCH = "IMF PortWatch (daily layers)"
# The exact IMF form: "Source: International Monetary Fund, <database>, <link>", with the transformation stated beside it.
PORTWATCH_ATTR = ("Source: International Monetary Fund, PortWatch, https://portwatch.imf.org"
                  " \u00b7 Transformed: 30-day windows, shares and capacity weights")
PORTWATCH_LICENCE = ("IMF data terms: copy, derive and publish with attribution and a statement of "
                     "transformations; bulk automated download and commercial reuse need IMF permission")
METEO_ATTR = "Weather data by Open-Meteo.com (CC BY 4.0)."
METEO_LICENCE = "CC BY 4.0; the free API tier is for non-commercial use"
EIA_ATTR = "Source: U.S. Energy Information Administration"
COPERNICUS_ATTR = "Contains modified Copernicus Sentinel data, ESA"
COPERNICUS_LICENCE = "Copernicus Sentinel data: free, full and open (Regulation (EU) No 1159/2013)"
MARINECADASTRE_ATTR = ("Source: U.S. Coast Guard Nationwide AIS via MarineCadastre.gov (BOEM and NOAA). "
                       "Vessel identifiers are replaced by salted hashes.")

# id -> definition. ``bundle`` is the key inside data/research/series.json.gz.
SERIES: dict[str, dict] = {
    "portwatch_port_calls": {
        "title": "Port calls per day", "kind": "port_calls", "unit": "calls/day",
        "applies_to": "port", "native": "portcalls", "source": PORTWATCH,
        "licence": PORTWATCH_LICENCE, "attribution": PORTWATCH_ATTR, "resolution": "daily"},
    "portwatch_port_calls_cargo": {
        "title": "Port calls per day, cargo vessels", "kind": "port_calls", "unit": "calls/day",
        "applies_to": "port", "native": "portcalls_cargo", "source": PORTWATCH,
        "licence": PORTWATCH_LICENCE, "attribution": PORTWATCH_ATTR, "resolution": "daily"},
    "portwatch_transits": {
        "title": "Transits per day", "kind": "transits", "unit": "vessels/day",
        "applies_to": "chokepoint", "native": "n_total", "source": PORTWATCH,
        "licence": PORTWATCH_LICENCE, "attribution": PORTWATCH_ATTR, "resolution": "daily"},
    "portwatch_transits_cargo": {
        "title": "Transits per day, cargo vessels", "kind": "transits", "unit": "vessels/day",
        "applies_to": "chokepoint", "native": "n_cargo", "source": PORTWATCH,
        "licence": PORTWATCH_LICENCE, "attribution": PORTWATCH_ATTR, "resolution": "daily"},
    "portwatch_transit_capacity": {
        "title": "Transit capacity (vessel deadweight)", "kind": "transits",
        "unit": "dwt-tonnes/day", "applies_to": "chokepoint", "native": "capacity",
        "source": PORTWATCH, "licence": PORTWATCH_LICENCE, "attribution": PORTWATCH_ATTR,
        "resolution": "daily"},
    "weather_wind_max_kmh": {
        "title": "Daily maximum wind speed at 10 m", "kind": "weather", "unit": "km/h",
        "applies_to": "any", "native": "wind_speed_10m_max",
        "source": "Open-Meteo Historical Weather API (ERA5 reanalysis, MODELLED atmosphere)",
        "licence": METEO_LICENCE, "attribution": METEO_ATTR, "resolution": "daily"},
    "weather_gust_max_kmh": {
        "title": "Daily maximum wind gust at 10 m", "kind": "weather", "unit": "km/h",
        "applies_to": "any", "native": "wind_gusts_10m_max",
        "source": "Open-Meteo Historical Weather API (ERA5 reanalysis, MODELLED atmosphere)",
        "licence": METEO_LICENCE, "attribution": METEO_ATTR, "resolution": "daily"},
    "weather_precip_mm": {
        "title": "Daily precipitation", "kind": "weather", "unit": "mm",
        "applies_to": "any", "native": "precipitation_sum",
        "source": "Open-Meteo Historical Weather API (ERA5 reanalysis, MODELLED atmosphere)",
        "licence": METEO_LICENCE, "attribution": METEO_ATTR, "resolution": "daily"},
    "weather_wave_height_m": {
        "title": "Daily maximum wave height (sea points only)", "kind": "weather", "unit": "m",
        "applies_to": "any", "native": "wave_height_max",
        "source": "Open-Meteo Marine API (wave model, MODELLED sea state)",
        "licence": METEO_LICENCE, "attribution": METEO_ATTR, "resolution": "daily"},
    "economic_eia_spot_brent": {
        "title": "Brent crude spot price", "kind": "economic", "unit": "USD/barrel",
        "applies_to": "any", "native": "RBRTE", "source": "U.S. Energy Information Administration",
        "licence": "US government work, public domain (EIA asks for acknowledgement)",
        "attribution": EIA_ATTR, "resolution": "daily (trading days)"},
    "economic_eia_spot_wti": {
        "title": "WTI crude spot price", "kind": "economic", "unit": "USD/barrel",
        "applies_to": "any", "native": "RWTC", "source": "U.S. Energy Information Administration",
        "licence": "US government work, public domain (EIA asks for acknowledgement)",
        "attribution": EIA_ATTR, "resolution": "daily (trading days)"},
}

# Served as not_served, with the reason a viewer can read.
NOT_SERVED: dict[str, dict] = {
    "economic_comtrade_exports": {
        "title": "UN Comtrade annual exports", "kind": "economic", "unit": "USD",
        "source": "UN Comtrade", "licence": "UN copyright; original records are not redistributed, derived shares only",
        "attribution": "Source: UN Comtrade (comtrade.un.org) \u00b7 Derived from UN Comtrade; not the original records",
        "resolution": "annual",
        "reason": "licence: record-level redistribution is restricted (THIRD-PARTY.md item 2); annual resolution does not fit a daily timeline"},
    "economic_fred": {
        "title": "FRED economic series", "kind": "economic", "unit": "various",
        "source": "Federal Reserve Bank of St. Louis (FRED)", "licence": "per-series terms",
        "attribution": "Source: FRED, Federal Reserve Bank of St. Louis", "resolution": "various",
        "reason": "no FRED data is cached in this repository and series licences differ per source"},
    "events_gdelt": {
        "title": "GDELT news-volume events", "kind": "events", "unit": "articles",
        "source": "The GDELT Project", "licence": "free and open with citation (verify)",
        "attribution": "Source: The GDELT Project (gdeltproject.org)", "resolution": "15 min",
        "reason": "the upstream may rate-limit requests (HTTP 429) and nothing is cached; "
                  "served only when a cached window exists or online fetching is enabled"},
}

EVENTS_CURATED = {
    "id": "events_curated", "title": "Curated, sourced episode events", "kind": "events",
    "unit": "events", "source": "SupermAP curated episode library (each event cites its publisher)",
    "licence": "factual dates; publishers and URLs cited per event",
    "attribution": "Event dates and descriptions cite the publisher named on each event.",
    "resolution": "event"}

SCENES = {
    "scenes_sentinel2": {
        "title": "Sentinel-2 L2A acquisitions", "kind": "scenes", "unit": "scenes",
        "source": "Copernicus Sentinel-2 L2A via Microsoft Planetary Computer STAC",
        "licence": COPERNICUS_LICENCE, "attribution": COPERNICUS_ATTR,
        "resolution": "scene (about every 5 days per tile)", "sensor": "sentinel-2"},
    "scenes_sentinel1": {
        "title": "Sentinel-1 GRD acquisitions", "kind": "scenes", "unit": "scenes",
        "source": "Copernicus Sentinel-1 GRD via Element84 Earth Search STAC",
        "licence": COPERNICUS_LICENCE, "attribution": COPERNICUS_ATTR,
        "resolution": "scene (about every 6 days per tile)", "sensor": "sentinel-1"},
}

TRACKS = {
    "id": "tracks_ais", "title": "Vessel tracks (open historical AIS)", "kind": "tracks",
    "unit": "positions", "source": "US Coast Guard Nationwide AIS via MarineCadastre.gov",
    "licence": "US government data, public domain", "attribution": MARINECADASTRE_ATTR,
    "resolution": "20 minutes (thinned)"}

# metric key -> dataset id, for the bundle builder
WEATHER_DATASETS = ["weather_wind_max_kmh", "weather_gust_max_kmh", "weather_precip_mm",
                    "weather_wave_height_m"]
PORTWATCH_DATASETS = ["portwatch_port_calls", "portwatch_port_calls_cargo", "portwatch_transits",
                      "portwatch_transits_cargo", "portwatch_transit_capacity"]
ECON_DATASETS = ["economic_eia_spot_brent", "economic_eia_spot_wti"]

SCENE_SENSORS = {"sentinel-2": "scenes_sentinel2", "sentinel-1": "scenes_sentinel1"}


def series_ids() -> list[str]:
    return list(SERIES) + [k for k, v in NOT_SERVED.items() if v["kind"] in ("economic",)]

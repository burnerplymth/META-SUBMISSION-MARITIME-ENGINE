"""Source registry for the multi-source satellite desk.

Four free, no-auth imagery sources, each with its resolution, nominal
revisit, licence / attribution and an honest ``ship_detection_grade``:

=============  ==========  ==========================================
``sentinel-2`` 10 m        floor-grade: a large hull is a few pixels
``sentinel-1`` ~20 m res   large vessels, all-weather (SAR, no cloud)
``landsat``    30 m        none: too coarse to count hulls
``naip``       0.6-1 m US  not a ship source: port-layout reference
=============  ==========  ==========================================

The grade is a statement about what the SOURCE can support, not about
what any detector in this repo has been validated to do. Counts from any
source are a floor, labelled MODELED, never SOURCED.

Pure data plus small helpers - no network. ``describe_sources()`` is
served by ``GET /satwatch/sources``.
"""
from __future__ import annotations

from typing import Optional

# Catalogue used for every source below (Earth Search, AWS Open Data).
EARTH_SEARCH_API = "https://earth-search.aws.element84.com/v1"
# Crop renderer (Planetary Computer titiler; same host the S2 pipeline uses).
PC_TITILER = "https://planetarycomputer.microsoft.com/api/data/v1/item"

GRADE_FLOOR = "floor"
GRADE_LARGE_ALL_WEATHER = "large_vessels_all_weather"
GRADE_NONE = "none"
GRADE_NOT_A_SHIP_SOURCE = "not_a_ship_source"

# Short words a chip can print for each grade (UI never needs prose).
GRADE_LABELS = {
    GRADE_FLOOR: "FLOOR · FEW PIXELS",
    GRADE_LARGE_ALL_WEATHER: "LARGE VESSELS · ALL-WEATHER",
    GRADE_NONE: "NO SHIP GRADE",
    GRADE_NOT_A_SHIP_SOURCE: "NOT A SHIP SOURCE",
}

# Sources where a ship detector exists in this repo and what it is.
SOURCES: list[dict] = [
    {
        "id": "sentinel-2",
        "chip": "S2",
        "label": "SENTINEL-2 L2A",
        "kind": "optical",
        "collection": "sentinel-2-l2a",
        "titiler_collection": "sentinel-2-l2a",
        "resolution_m": 10,
        "revisit_days": 5,
        "revisit_label": "~5 D",
        "coverage": "global",
        "cloud_filter": True,
        "ship_detection_grade": GRADE_FLOOR,
        "ship_detection_label": GRADE_LABELS[GRADE_FLOOR],
        "detector": "scan pipeline (provider seam)",
        "licence": "Copernicus Sentinel data: free, full and open "
                   "(Regulation (EU) No 1159/2013)",
        "attribution": "Contains modified Copernicus Sentinel data, ESA",
        "attribution_chip": "COPERNICUS · ESA",
    },
    {
        "id": "sentinel-1",
        "chip": "S1 SAR",
        "label": "SENTINEL-1 GRD",
        "kind": "sar",
        "collection": "sentinel-1-grd",
        "titiler_collection": "sentinel-1-grd",
        "resolution_m": 20,
        "pixel_spacing_m": 10,
        "polarisation": "VV",
        "revisit_days": 6,
        "revisit_label": "~6 D",
        "coverage": "global",
        "cloud_filter": False,
        "ship_detection_grade": GRADE_LARGE_ALL_WEATHER,
        "ship_detection_label": GRADE_LABELS[GRADE_LARGE_ALL_WEATHER],
        "detector": "sentinel1-cfar/1 (cell-averaging CFAR, VV amplitude)",
        "licence": "Copernicus Sentinel data: free, full and open "
                   "(Regulation (EU) No 1159/2013)",
        "attribution": "Contains modified Copernicus Sentinel data, ESA",
        "attribution_chip": "COPERNICUS · ESA",
    },
    {
        "id": "landsat",
        "chip": "LANDSAT",
        "label": "LANDSAT C2 L2",
        "kind": "optical",
        "collection": "landsat-c2-l2",
        "titiler_collection": "landsat-c2-l2",
        "resolution_m": 30,
        "revisit_days": 8,
        "revisit_label": "~8 D",
        "coverage": "global",
        "cloud_filter": True,
        "ship_detection_grade": GRADE_NONE,
        "ship_detection_label": GRADE_LABELS[GRADE_NONE],
        "detector": None,
        "licence": "USGS Landsat data: public domain, no restrictions on use",
        "attribution": "Landsat imagery courtesy of the U.S. Geological Survey",
        "attribution_chip": "USGS",
    },
    {
        "id": "naip",
        "chip": "NAIP",
        "label": "NAIP AERIAL",
        "kind": "aerial",
        "collection": "naip",
        "titiler_collection": "naip",
        "resolution_m": 0.6,
        "revisit_days": None,
        "revisit_label": "INFREQUENT",
        "coverage": "US",
        "cloud_filter": False,
        "ship_detection_grade": GRADE_NOT_A_SHIP_SOURCE,
        "ship_detection_label": GRADE_LABELS[GRADE_NOT_A_SHIP_SOURCE],
        "detector": None,
        "licence": "USDA NAIP imagery: public domain",
        "attribution": "Imagery courtesy of USDA Farm Service Agency (NAIP), via USGS",
        "attribution_chip": "USDA · NAIP",
    },
]

SOURCE_IDS = tuple(s["id"] for s in SOURCES)
_BY_ID = {s["id"]: s for s in SOURCES}

# Search window per source, days back from today. NAIP has no window:
# acquisitions are years apart, so the newest tile is what is wanted.
DEFAULT_WINDOW_DAYS = {"sentinel-2": 60, "sentinel-1": 30, "landsat": 60, "naip": None}
# Optical sources are screened on the catalogue's scene cloud value.
DEFAULT_MAX_CLOUD = {"sentinel-2": 60, "landsat": 60}

# Rough US coverage boxes [min_lon, min_lat, max_lon, max_lat]: CONUS,
# Alaska, Hawaii, Puerto Rico / USVI. A pre-check so a non-US place
# answers OUT OF COVER without a catalogue call; the catalogue itself is
# still the authority for whether a tile exists.
US_BOXES = (
    [-125.0, 24.0, -66.5, 49.6],
    [-170.0, 51.0, -129.0, 71.6],
    [-160.6, 18.7, -154.7, 22.4],
    [-68.0, 17.5, -64.4, 18.8],
)


def get_source(source_id: str) -> dict:
    try:
        return _BY_ID[source_id]
    except KeyError:
        raise KeyError(f"unknown source {source_id!r}") from None


def describe_sources() -> dict:
    return {
        "contract": "satwatch-sources/1",
        "catalog": EARTH_SEARCH_API,
        "mode": "SNAPSHOT",
        "sources": [dict(s) for s in SOURCES],
        "grades": dict(GRADE_LABELS),
        "counts_basis": "BRIGHT_TARGETS",
    }


def in_us_coverage(bbox: list) -> bool:
    """True when the bbox centre sits inside a US coverage box."""
    cx = (bbox[0] + bbox[2]) / 2.0
    cy = (bbox[1] + bbox[3]) / 2.0
    return any(b[0] <= cx <= b[2] and b[1] <= cy <= b[3] for b in US_BOXES)


def covered_by_region(source_id: str, bbox: list) -> bool:
    src = get_source(source_id)
    if src["coverage"] == "US":
        return in_us_coverage(bbox)
    return True


def max_cloud_for(source_id: str, override: Optional[float] = None) -> Optional[float]:
    """Cloud ceiling, or None where a cloud filter does not apply."""
    if not get_source(source_id)["cloud_filter"]:
        return None
    return DEFAULT_MAX_CLOUD.get(source_id) if override is None else override


# ---------------------------------------------------------------------------
# Aerial (sub-metre) sources for the SAM path
# ---------------------------------------------------------------------------
# Reachability, licence and layer facts below were probed against the live
# services (HTTP 200 GetCapabilities and GetMap). They are kept apart from ``SOURCES``: the
# Sentinel/Landsat desk iterates SOURCES per place and these are not
# per-place catalogue sources. An aerial scene is always an AERIAL SURVEY
# of a stated year, a snapshot, never live. Native pixels only: the request
# asks the service for exactly the declared ground sampling distance and
# nothing is resampled on our side.
AERIAL_MAX_SIDE_PX = 2048
AERIAL_MAX_SCENE_PX = 2048 * 2048
AERIAL_SOURCES: list[dict] = [
    {
        "id": "naip", "chip": "NAIP", "label": "NAIP AERIAL", "access": "naip_titiler",
        "gsd_m": 0.6, "coverage": "US", "year": None, "year_basis": "item date",
        "epsg_note": "lon/lat crop through the Planetary Computer titiler /crop/ route",
        "licence": "USDA NAIP imagery: public domain",
        "attribution": "Imagery courtesy of USDA Farm Service Agency (NAIP), via USGS",
        "attribution_chip": "USDA · NAIP",
    },
    {
        "id": "pdok-nl", "chip": "PDOK 25 CM", "label": "PDOK LUCHTFOTO 25 CM",
        "access": "wms", "wms_url": "https://service.pdok.nl/hwh/luchtfotorgb/wms/v1_0",
        "layer": "2025_ortho25", "format": "image/jpeg", "epsg_fixed": 25831,
        "gsd_m": 0.25, "coverage": "NL", "year": 2025, "year_basis": "layer name",
        "licence": "CC BY 4.0 (PDOK service metadata: open data, cc-by)",
        "attribution": "Luchtfoto 2025, Beeldmateriaal Nederland, via PDOK (CC BY 4.0)",
        "attribution_chip": "PDOK · CC BY",
    },
    {
        "id": "omw-be", "chip": "OMW 15 CM", "label": "FLANDERS ORTHOPHOTO 15 CM",
        "access": "wms", "wms_url": "https://geo.api.vlaanderen.be/OMW/wms",
        "layer": "OMWRGB25VL", "format": "image/png", "epsg_fixed": 25831,
        "gsd_m": 0.15, "coverage": "BE-Flanders", "year": 2025, "year_basis": "layer name (winter flight)",
        "licence": "Digitaal Vlaanderen geographic web services terms of use (free of charge; terms at vlaanderen.be)",
        "attribution": "Orthophoto mosaic 2025, Digitaal Vlaanderen",
        "attribution_chip": "DIGITAAL VLAANDEREN",
    },
    {
        "id": "ign-fr", "chip": "IGN ORTHO", "label": "IGN ORTHOPHOTO",
        "access": "wms", "wms_url": "https://data.geopf.fr/wms-r/wms",
        "layer": "ORTHOIMAGERY.ORTHOPHOTOS", "format": "image/png", "epsg_fixed": 32631,
        "gsd_m": 0.2, "coverage": "FR", "year": None, "year_basis": "mosaic, year varies by area (not stated by the service)",
        "licence": "Licence Ouverte / Etalab 2.0 (confirm against the product metadata before shipping a scene)",
        "attribution": "Source: IGN, BD ORTHO (production year per scene)",
        "attribution_chip": "IGN",
    },
    {
        "id": "pnoa-es", "chip": "PNOA", "label": "PNOA ORTHOPHOTO",
        "access": "wms", "wms_url": "https://www.ign.es/wms-inspire/pnoa-ma",
        "layer": "OI.OrthoimageCoverage", "format": "image/png", "epsg_by_lon": {"lt0": 25830, "ge0": 25831},
        "gsd_m": 0.5, "coverage": "ES", "year": None,
        "year_basis": "mosaic of different dates and 0.25 to 0.5 m sources (not stated per tile)",
        "licence": "CC BY 4.0 (service metadata: CC BY 4.0 scne.es)",
        "attribution": "PNOA orthophotography, Instituto Geografico Nacional de Espana (IGN), CC BY 4.0",
        "attribution_chip": "IGN ES · CC BY",
    },
]
AERIAL_BY_ID = {s["id"]: s for s in AERIAL_SOURCES}


def get_aerial_source(source_id: str) -> dict:
    try:
        return AERIAL_BY_ID[source_id]
    except KeyError:
        raise KeyError(f"unknown aerial source {source_id!r}") from None


def describe_aerial_sources() -> dict:
    return {
        "contract": "satwatch-aerial-sources/1", "mode": "SNAPSHOT",
        "max_side_px": AERIAL_MAX_SIDE_PX,
        "sources": [{k: v for k, v in s.items() if k not in ("wms_url", "layer", "format", "epsg_fixed", "epsg_by_lon")}
                    for s in AERIAL_SOURCES],
    }

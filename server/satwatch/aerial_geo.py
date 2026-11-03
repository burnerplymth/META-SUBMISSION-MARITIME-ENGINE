"""Small geodesy helpers for the aerial (sub-metre) path: WGS84 <-> UTM and
the metres-per-degree used for NAIP's lon/lat crops. Pure functions, no
network, no dependencies (pyproj is not installed on this server).

UTM uses the Kruger series (accurate to well under a millimetre inside a
zone); on the central meridian lat 45 maps to N 4 982 950.40 m, and the
forward and inverse transforms round-trip.
"""
from __future__ import annotations

import math

_A = 6378137.0
_F = 1 / 298.257223563
_K0 = 0.9996
_N = _F / (2 - _F)
_AA = _A / (1 + _N) * (1 + _N ** 2 / 4 + _N ** 4 / 64)
_ALPHA = (
    _N / 2 - 2 * _N ** 2 / 3 + 5 * _N ** 3 / 16,
    13 * _N ** 2 / 48 - 3 * _N ** 3 / 5,
    61 * _N ** 3 / 240,
)
_BETA = (
    _N / 2 - 2 * _N ** 2 / 3 + 37 * _N ** 3 / 96,
    _N ** 2 / 48 + _N ** 3 / 15,
    17 * _N ** 3 / 480,
)
_DELTA = (
    2 * _N - 2 * _N ** 2 / 3 - 2 * _N ** 3,
    7 * _N ** 2 / 3 - 8 * _N ** 3 / 5,
    56 * _N ** 3 / 15,
)
FALSE_EASTING = 500000.0


def utm_zone(lon: float) -> int:
    return int((lon + 180.0) // 6) + 1


def central_meridian(zone: int) -> float:
    return zone * 6.0 - 183.0


def utm_forward(lon: float, lat: float, zone: int) -> tuple[float, float]:
    """(easting, northing) in metres, northern hemisphere."""
    phi, lam = math.radians(lat), math.radians(lon - central_meridian(zone))
    e = math.sqrt(_F * (2 - _F))
    t = math.sinh(math.atanh(math.sin(phi)) - e * math.atanh(e * math.sin(phi)))
    xi_p = math.atan2(t, math.cos(lam))
    eta_p = math.atanh(math.sin(lam) / math.sqrt(1 + t * t))
    xi = xi_p + sum(a * math.sin(2 * (j + 1) * xi_p) * math.cosh(2 * (j + 1) * eta_p) for j, a in enumerate(_ALPHA))
    eta = eta_p + sum(a * math.cos(2 * (j + 1) * xi_p) * math.sinh(2 * (j + 1) * eta_p) for j, a in enumerate(_ALPHA))
    return FALSE_EASTING + _K0 * _AA * eta, _K0 * _AA * xi


def utm_inverse(easting: float, northing: float, zone: int) -> tuple[float, float]:
    """(lon, lat) in degrees, northern hemisphere."""
    xi = northing / (_K0 * _AA)
    eta = (easting - FALSE_EASTING) / (_K0 * _AA)
    xi_p = xi - sum(b * math.sin(2 * (j + 1) * xi) * math.cosh(2 * (j + 1) * eta) for j, b in enumerate(_BETA))
    eta_p = eta - sum(b * math.cos(2 * (j + 1) * xi) * math.sinh(2 * (j + 1) * eta) for j, b in enumerate(_BETA))
    chi = math.asin(math.sin(xi_p) / math.cosh(eta_p))
    phi = chi + sum(d * math.sin(2 * (j + 1) * chi) for j, d in enumerate(_DELTA))
    lam = math.atan2(math.sinh(eta_p), math.cos(xi_p))
    return central_meridian(zone) + math.degrees(lam), math.degrees(phi)


def metres_per_degree(lat: float) -> tuple[float, float]:
    """(metres per degree of longitude, of latitude) at ``lat`` (spherical, as multistac)."""
    return 111_320.0 * math.cos(math.radians(lat)), 110_540.0

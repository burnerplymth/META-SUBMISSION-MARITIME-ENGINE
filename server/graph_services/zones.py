"""Coarse sea zones for placing stated service-level routing.

MODELLED, geographic, deliberately crude. Used ONLY to decide which legs
of a service cross the Suez basin (or the Cape) when the source states
the routing for the whole service ("operates through the Suez Canal")
but prints no marker position, and to order Bab el-Mandeb relative to
Suez on a Suez-routed leg. It is never used to invent a service, a call
or a stated chokepoint.

  E   east of the Suez basin: Indian Ocean, Gulf, Asia, Oceania, East and
      South Africa, and the Gulf of Aden side of Bab el-Mandeb
  RS  Red Sea proper (north of Bab el-Mandeb, south of the canal)
  W   Mediterranean, Black Sea, Europe, Atlantic, Atlantic Americas
  P   Pacific Americas (not crossed by a Suez or Cape routing)
"""
from __future__ import annotations

RS_LAT = (12.7, 30.6)
RS_LON = (32.0, 44.5)


def zone(lat: float, lon: float) -> str:
    lat = float(lat)
    lon = float(lon)
    if RS_LAT[0] <= lat <= RS_LAT[1] and RS_LON[0] <= lon <= RS_LON[1]:
        return "RS"
    if lon <= -100.0:
        return "P"
    if lon < -30.0:
        return "W"
    if lat < -10.0 and lon >= 10.0:
        return "E"
    if lon <= 32.5:
        return "W"
    if lat >= 30.6 and lon <= 42.0:
        return "W"           # Levant, Anatolia, Black Sea
    return "E"


def crosses_suez(zone_a: str, zone_b: str) -> bool:
    """A leg between the Suez basin's two sides ({E, RS} and W)."""
    return {zone_a, zone_b} <= {"E", "RS", "W"} and "W" in (zone_a, zone_b) \
        and bool({zone_a, zone_b} & {"E", "RS"})


def crosses_cape(zone_a: str, zone_b: str) -> bool:
    """A leg between the Indian-Ocean side and the Atlantic/Med side."""
    return {zone_a, zone_b} == {"E", "W"}

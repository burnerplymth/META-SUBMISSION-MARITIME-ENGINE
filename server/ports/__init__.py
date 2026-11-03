"""PORTS TRACKER.

One board row per place (sea ports, chokepoints, air hubs, ground
places) merging what the repository really holds: PortWatch window
means + annual baseline, CBP border waits, OpenSky hub aircraft
counts, AIS samples, the events feed, the satwatch scan, and — when a
run id is given — the run's simulated state and chain exposure.

Doctrine carried by every cell (see cells.py): a provenance chip
(SOURCED / MODELLED / SNAPSHOT / SIMULATED / NOT SERVED), its own as-of,
and an explicit NOT SERVED cell — never a zero — when data is absent.
There is no total across places, no cross-class aggregate, and no
forward claim anywhere in this package.
"""

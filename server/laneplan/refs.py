"""Cited real-world references for the lane planner's distance sanity check.

Only figures that were opened in a source are listed. Nothing here is an
engine value; the planner compares the engine's route lengths against these
and flags what falls outside (it never hides or adjusts the engine figure).

UNCTAD (February 2024), "Navigating troubled waters: impact to global trade
of disruption of shipping routes in the Red Sea, Black Sea and Panama Canal",
UNCTAD rapid assessment, page 10:

  "an oil tanker going from the port of Ras Tanura in Saudi Arabia to
   Rotterdam ... will have to travel 10,358 km through the Suez Canal. The
   alternative journey via the Cape of Good Hope would be 17,975 km. This cuts
   the journey length by 42 per cent. Similarly, a container shipped from
   Singapore to Rotterdam sees the journey cut by 29 per cent when using the
   Suez instead of the Cape of Good Hope."

A "cut" is (Cape - Suez) / Cape. For the Ras Tanura pair the cited km give
42.37 %; the Singapore pair is cited as a whole percent, so its interval is
the rounding half-width, 28.5 to 29.5.
"""
from __future__ import annotations

UNCTAD_ID = "unctad_2024"
UNCTAD = {
    "id": UNCTAD_ID,
    "title": "UNCTAD Navigating troubled waters (Feb 2024), p. 10",
    "url": "https://unctad.org/node/44141",
    "state": "SOURCED",
}

RAS_TANURA_SUEZ_KM = 10358.0
RAS_TANURA_CAPE_KM = 17975.0

# Cited cut of the journey by using Suez instead of the Cape, in percent.
RAS_TANURA_CUT_PCT = round((RAS_TANURA_CAPE_KM - RAS_TANURA_SUEZ_KM) / RAS_TANURA_CAPE_KM * 100.0, 3)
SINGAPORE_CUT_PCT = 29.0
SINGAPORE_ROUNDING = 0.5

# Pair-specific cited references (origin, destination) -> reference row.
PAIR_REFS = {
    ("sea_singapore", "sea_rotterdam"): {
        "id": "unctad_singapore_rotterdam", "label": "Singapore to Rotterdam",
        "cut_pct": {"lo": SINGAPORE_CUT_PCT - SINGAPORE_ROUNDING, "hi": SINGAPORE_CUT_PCT + SINGAPORE_ROUNDING},
        "source": UNCTAD_ID},
}

# The corridor interval: the smallest and largest of the two cited cuts, for
# Europe-bound pairs from the Gulf and from Asia that use Suez or the Cape.
CORRIDOR_REF = {
    "id": "unctad_corridor", "label": "Gulf and Asia to Rotterdam, two cited pairs",
    "cut_pct": {"lo": SINGAPORE_CUT_PCT - SINGAPORE_ROUNDING, "hi": RAS_TANURA_CUT_PCT},
    "source": UNCTAD_ID,
}

"""The "Detected, not verified" stamp for stored-scan counts.

Every count a detector produced wears this until a human has reviewed that
place (nothing sets ``human_reviewed`` yet). The aerial, SAM, scene, brief and
signal paths already carry it; this helper puts the same two fields beside
the count objects of the stored-scan overlay, the scan-job items and the
Sentinel-1 records. Additive: no existing key changes.
"""
from __future__ import annotations

VERIFICATION = "Detected, not verified"


def stamp(counts) -> dict:
    """{"verification", "human_reviewed"} for a count object; no counts, no label."""
    return {"verification": VERIFICATION if counts is not None else None, "human_reviewed": False}

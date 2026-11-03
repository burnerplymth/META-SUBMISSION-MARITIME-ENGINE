"""Satellite Watch - Sentinel-2 AOI scans + (seamed) SAM detection.

Imagery side is live today (Microsoft Planetary Computer, zero auth);
detection is behind the provider seam in ``detect`` and ships in fixture
mode (counts null, status AWAITING_META_KEY) until the Meta Model API
key is set.
"""

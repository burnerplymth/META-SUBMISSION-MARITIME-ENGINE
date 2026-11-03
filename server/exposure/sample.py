"""A synthetic sample portfolio so the Shipments window works with no user data.

Every row is invented: ids ``SAMPLE-001`` onward, lanes between large container ports, values and
quantities from a fixed formula (no random numbers, no clock), so the list is identical on every call.
Nothing here describes a real consignment, shipper or consignee.
"""
from __future__ import annotations

SAMPLE_LABEL = "Sample portfolio (synthetic)"

# (origin text, destination text, mode, n shipments, base value USD, unit, quantity, via text or None)
_LANES = [
    ("Shanghai", "Rotterdam", "sea", 4, 180_000, "TEU", 6, None),
    ("Shanghai", "Hamburg", "sea", 3, 240_000, "TEU", 8, None),
    ("Singapore", "Port of Rotterdam", "sea", 3, 150_000, "TEU", 5, None),
    ("Ningbo", "Rotterdam", "sea", 2, 210_000, "TEU", 7, None),
    ("Colombo", "Rotterdam", "sea", 1, 70_000, "TEU", 2, None),
    ("Nhava Sheva", "NLRTM", "sea", 2, 130_000, "TEU", 4, None),
    ("Mundra", "Felixstowe", "sea", 2, 120_000, "TEU", 4, None),
    ("Shenzhen", "Genoa", "sea", 2, 195_000, "TEU", 6, None),
    ("Hong Kong", "Barcelona", "sea", 1, 95_000, "TEU", 3, None),
    ("Busan", "Piraeus", "sea", 1, 112_000, "TEU", 4, None),
    ("Port Klang", "Valencia", "sea", 1, 88_000, "TEU", 3, None),
    ("Jebel Ali", "Genoa", "sea", 2, 110_000, "TEU", 4, None),
    ("Jeddah", "Rotterdam", "sea", 1, 64_000, "TEU", 2, None),
    ("Shanghai", "New York", "sea", 3, 230_000, "TEU", 8, None),
    ("Ningbo", "New York", "sea", 1, 205_000, "TEU", 7, None),
    ("Busan", "Savannah", "sea", 2, 150_000, "TEU", 5, None),
    ("Shenzhen", "Houston", "sea", 1, 165_000, "TEU", 6, None),
    ("Shanghai", "Los Angeles", "sea", 3, 260_000, "TEU", 9, None),
    ("Busan", "Long Beach", "sea", 2, 175_000, "TEU", 6, None),
    ("Rotterdam", "New York", "sea", 2, 140_000, "TEU", 5, None),
    ("Hamburg", "Savannah", "sea", 2, 125_000, "TEU", 4, None),
    ("Rotterdam", "Shanghai", "sea", 2, 135_000, "TEU", 5, None),
    ("Singapore", "Jebel Ali", "sea", 1, 60_000, "TEU", 2, None),
    ("Shanghai Pudong", "Frankfurt am Main", "air", 2, 320_000, "kg", 2_400, None),
    ("Hong Kong International", "London Heathrow", "air", 1, 410_000, "kg", 1_800, None),
    ("Inland Empire", "Chicago Intermodal Cluster", "ground", 1, 52_000, "TEU", 2, None),
]
# presentation order: interleaved, not grouped, so the list reads like a purchase-order extract
_ORDER_STEP = 7


def sample_shipments() -> list[dict]:
    rows = []
    for (o, d, mode, n, base, unit, qty, via) in _LANES:
        for j in range(n):
            rows.append({"origin": o, "destination": d, "mode": mode, "value_usd": float(base + 7_500 * j),
                         "quantity": float(qty + j), "unit": unit, "via": via})
    k = len(rows)
    order = []
    i = 0
    seen = set()
    while len(order) < k:                 # a fixed stride through the rows (coprime with k)
        if i not in seen:
            seen.add(i)
            order.append(i)
        i = (i + _ORDER_STEP) % k
        if i in seen and len(order) < k:
            i = next(x for x in range(k) if x not in seen)
    out = []
    for n, idx in enumerate(order, 1):
        r = dict(rows[idx])
        r["id"] = f"SAMPLE-{n:03d}"
        r = {"id": r["id"], **{k2: v for k2, v in r.items() if k2 != "id" and v is not None}}
        out.append(r)
    return out


def sample_document() -> dict:
    ships = sample_shipments()
    return {"label": SAMPLE_LABEL, "n": len(ships), "shipments": ships}

"""Figure objects: the only numeric shape this layer serves.

Every figure is {value, unit, class, source, as_of, derivation, label,
chips}. `derivation`, `reason`, `label` and `chips` are SHORT: a derivation
is a one-line working (`30d mean · <start date> → <end date>`), never a
paragraph; `label` names the quantity in a few words and `chips` are the
state tokens the panel draws as chips (`30D MEAN`, `AIS ESTIMATE`). An
ABSENT figure has value None and carries a short `reason` — an absence is
rendered as an absence line, never as a zero. figure()
validates the class against the closed set and refuses unit strings
that would smuggle a conversion (a figure's unit names exactly one
quantity in exactly one unit class).
"""
from __future__ import annotations

FIGURE_CLASSES = ("SOURCED", "MODELLED", "HISTORICAL", "ABSENT")

# Field-name fragments that must never appear anywhere in an impact
# payload: a field named like these could
# carry a cross-class aggregate or a converted value — the schema
# refuses them outright. "usd" is banned as a VALUE unit for derived
# figures; Comtrade citation records keep their source field names
# (value_usd) only inside the raw extracts file, never in payloads —
# payload citations carry value + unit "USD (CIF value, as reported)".
FORBIDDEN_FIELD_PARTS = (
    "trade_at_risk", "trade-at-risk", "value_at_risk", "total_impact",
    "impact_score", "usd_at_risk", "tonnes_equivalent", "teu_equivalent",
    "converted", "price_change", "price_impact", "expected_price",
    "rate_forecast", "price_forecast",
)

_FIGURE_KEYS = ("value", "unit", "class", "source", "as_of", "derivation")

# The longest a served derivation / reason / label may be.
MAX_SHORT_TEXT = 100


def figure(value, unit, cls, source, as_of, derivation, label=None,
           chips=None) -> dict:
    if cls not in FIGURE_CLASSES:
        raise ValueError(f"unknown figure class {cls!r}")
    if cls == "ABSENT":
        raise ValueError("use absent() for ABSENT figures")
    if value is None:
        raise ValueError("a non-ABSENT figure needs a value; use absent()")
    return {"value": value, "unit": unit, "class": cls, "source": source,
            "as_of": as_of, "derivation": derivation,
            "label": label if label is not None else unit,
            "chips": list(chips) if chips else []}


def absent(reason, unit=None, source=None, as_of=None, label=None) -> dict:
    return {"value": None, "unit": unit, "class": "ABSENT", "source": source,
            "as_of": as_of,
            "derivation": "not served",
            "label": label if label is not None else "NOT SERVED",
            "chips": ["NOT SERVED"],
            "reason": reason}


def is_figure(obj) -> bool:
    return (isinstance(obj, dict)
            and all(k in obj for k in _FIGURE_KEYS)
            and obj.get("class") in FIGURE_CLASSES)


def assert_payload_honest(payload, path: str = "$") -> None:
    """Recursive schema guard: no field
    name anywhere in a served payload may match the forbidden list,
    and every dict that looks like a figure (has value+class keys)
    must be a well-formed Figure."""
    if isinstance(payload, dict):
        if {"value", "class"} <= set(payload) and not is_figure(payload):
            raise ValueError(f"malformed figure at {path}: any object with "
                             f"value+class keys must be a complete Figure")
        for key, val in payload.items():
            low = str(key).lower()
            for frag in FORBIDDEN_FIELD_PARTS:
                if frag in low:
                    raise ValueError(
                        f"forbidden field {key!r} at {path}: a payload field "
                        f"capable of carrying a cross-class aggregate or a "
                        f"converted/forward figure must not exist")
            assert_payload_honest(val, f"{path}.{key}")
    elif isinstance(payload, list):
        for i, item in enumerate(payload):
            assert_payload_honest(item, f"{path}[{i}]")

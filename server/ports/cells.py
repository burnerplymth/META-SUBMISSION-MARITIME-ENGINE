"""Cell constructors: the only shape a datum leaves the ports tracker in.

A cell is {value, unit, chip, class, feed_state, source, as_of, note}.
`chip` is the provenance chip the UI prints:

  SOURCED      read from a published source that is answering now (LIVE
               feed, or a static census figure published as such)
  SNAPSHOT     read from a stored, dated copy of a source (feed state
               SNAPSHOT, or a LIVE record that has aged past its feed's
               freshness window)
  MODELLED     derived by this product (rule, estimate, chain run)
  SIMULATED    a value from a stored simulation run
  NOT SERVED   no data: `value` is None and `reason` says why — an
               absent datum is never rendered as a zero

`class` is the underlying provenance class (SOURCED / MODELLED /
SIMULATED / NOT SERVED); the chip only differs from it when a SOURCED
datum is served from a stored snapshot (chip SNAPSHOT).
"""
from __future__ import annotations

CHIPS = ("SOURCED", "MODELLED", "SNAPSHOT", "SIMULATED", "NOT SERVED")
CLASSES = ("SOURCED", "MODELLED", "SIMULATED", "NOT SERVED")
FEED_STATES = ("LIVE", "SNAPSHOT", "NOT SERVED")
CELL_KEYS = ("value", "unit", "chip", "class", "feed_state", "source",
             "as_of", "note")


def cell(value, unit, cls, source, as_of=None, feed_state=None, note=None,
         **extra) -> dict:
    """A served datum. `value` must not be None (use not_served())."""
    if cls not in CLASSES or cls == "NOT SERVED":
        raise ValueError(f"bad cell class {cls!r}")
    if value is None:
        raise ValueError("a served cell needs a value; use not_served()")
    chip = cls
    if cls == "SOURCED" and feed_state == "SNAPSHOT":
        chip = "SNAPSHOT"
    out = {"value": value, "unit": unit, "chip": chip, "class": cls,
           "feed_state": feed_state, "source": source, "as_of": as_of,
           "note": note}
    out.update(extra)
    return out


def not_served(reason: str, unit=None, source=None, as_of=None,
               feed_state="NOT SERVED") -> dict:
    return {"value": None, "unit": unit, "chip": "NOT SERVED",
            "class": "NOT SERVED", "feed_state": feed_state,
            "source": source, "as_of": as_of, "note": None,
            "reason": reason}


def is_cell(obj) -> bool:
    return (isinstance(obj, dict) and all(k in obj for k in CELL_KEYS)
            and obj["chip"] in CHIPS)


def from_figure(fig: dict, feed_state=None) -> dict:
    """Impact-layer Figure -> cell (ABSENT -> NOT SERVED)."""
    if fig.get("class") == "ABSENT" or fig.get("value") is None:
        return not_served(fig.get("reason") or "no figure is served",
                          unit=fig.get("unit"), source=fig.get("source"),
                          as_of=fig.get("as_of"))
    cls = fig["class"]
    if cls == "HISTORICAL":
        cls = "SOURCED"
    return cell(fig["value"], fig.get("unit"), cls, fig.get("source"),
                fig.get("as_of"), feed_state=feed_state,
                note=fig.get("derivation"))


def walk_cells(payload, path="$"):
    """Yield (path, dict) for every cell-shaped object (has chip+class)."""
    if isinstance(payload, dict):
        if "chip" in payload and "class" in payload:
            yield path, payload
        for k, v in payload.items():
            yield from walk_cells(v, f"{path}.{k}")
    elif isinstance(payload, list):
        for i, v in enumerate(payload):
            yield from walk_cells(v, f"{path}[{i}]")


def assert_cells_honest(payload) -> None:
    """Every cell-shaped object is a complete cell; a None value is
    always chip NOT SERVED with a reason (never a silent zero), and a
    NOT SERVED chip never carries a value."""
    for path, c in walk_cells(payload):
        if not is_cell(c):
            raise ValueError(f"malformed cell at {path}")
        if c["value"] is None:
            if c["chip"] != "NOT SERVED" or not c.get("reason"):
                raise ValueError(f"None value without NOT SERVED+reason "
                                 f"at {path}")
        elif c["chip"] == "NOT SERVED":
            raise ValueError(f"NOT SERVED cell carries a value at {path}")

"""Loader + validator for the services sidecar (data/graph/services.json).

Validation is strict and non-repairing: an entry that fails any check is
DROPPED (and listed in the report with its reasons); nothing is guessed,
re-mapped or patched. A document-level failure (wrong schema, graph pin
does not match the graph in use, content hash mismatch) refuses the whole
file (ServicesError).

Checks per entry
  1. every call is an existing place id, mode sea, kind port or chokepoint
  2. at least two calls, no empty ids; gaps are sorted, unique, in range
  3. every via chokepoint exists in the graph with kind chokepoint, mode sea
  4. via positions are in range and non-decreasing (ordered)
  5. era in the registered vocabulary; calibration.basis SOURCED with a
     non-empty excerpt and registered source ids
  6. service_id unique

Selection (opt-in, journaled): eras, include_unverified (default False:
the 21 entries flagged webfetch_not_machine_verified are excluded),
include_unstated (default False: entries with no positive routing
statement, conflicting or indirect routing are excluded).
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_PATH = REPO_ROOT / "data" / "graph" / "services.json"
SCHEMA = "supermap.graph_services/1"
ERAS = ("pre_red_sea_crisis", "red_sea_diversion", "partial_suez_return_2026")
ERA_SET = frozenset(ERAS)
BASES = ("marker_in_rotation_string", "article_text", "article_text_indirect",
         "era_default_via", "conflicting_sources", "unstated")

# auto-era boundaries (published-era definitions of the research lane)
CRISIS_START = "2023-11-01"
RETURN_START = "2026-07-01"


class ServicesError(ValueError):
    """The sidecar is unusable as a whole (never a per-entry problem)."""


def services_path() -> Path:
    """SUPERMAP_SERVICES_PATH, else <SUPERMAP_DATA_DIR>/graph/services.json
    when that exists, else the repository's data/graph/services.json."""
    env = os.environ.get("SUPERMAP_SERVICES_PATH")
    if env:
        return Path(env)
    data_dir = os.environ.get("SUPERMAP_DATA_DIR")
    if data_dir:
        p = Path(data_dir) / "graph" / "services.json"
        if p.exists():
            return p
    return DEFAULT_PATH


def file_sha256(path: Path | None = None) -> str:
    return hashlib.sha256((path or services_path()).read_bytes()).hexdigest()


def _canonical(obj) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def content_sha(doc: dict) -> str:
    body = {k: v for k, v in doc.items() if k != "content_sha256"}
    return hashlib.sha256(_canonical(body).encode("utf-8")).hexdigest()


def auto_eras(start_at: str) -> list[str]:
    """Era set for a run starting at ``start_at`` (ISO-8601 'YYYY-MM-DD...')."""
    day = str(start_at)[:10]
    if day < CRISIS_START:
        return ["pre_red_sea_crisis"]
    if day < RETURN_START:
        return ["red_sea_diversion"]
    return ["partial_suez_return_2026", "red_sea_diversion"]


def validate_entry(e: dict, nodes: dict, seen: set) -> list[str]:
    """Reasons the entry is invalid (empty list = valid)."""
    bad: list[str] = []
    sid = e.get("service_id")
    if not isinstance(sid, str) or not sid:
        return ["missing service_id"]
    if sid in seen:
        bad.append("duplicate service_id")
    if e.get("mode") != "sea":
        bad.append(f"mode {e.get('mode')!r} is not sea")
    calls = e.get("calls")
    if not isinstance(calls, list) or len(calls) < 2:
        return bad + ["fewer than two calls"]
    for i, pid in enumerate(calls):
        n = nodes.get(pid) if isinstance(pid, str) else None
        if n is None:
            bad.append(f"call {i}: place {pid!r} not in graph")
        elif n.get("mode") != "sea" or n.get("kind") not in ("port", "chokepoint"):
            bad.append(f"call {i}: place {pid!r} is not a sea port")
    gaps = e.get("gaps", [])
    if (not isinstance(gaps, list) or gaps != sorted(set(gaps))
            or any((not isinstance(g, int)) or not 0 <= g < len(calls) - 1 for g in gaps)):
        bad.append("gaps not sorted/unique/in range")
    last_pos = -1
    for v in e.get("via", []):
        chk = v.get("chk")
        n = nodes.get(chk)
        if n is None or n.get("kind") != "chokepoint" or n.get("mode") != "sea":
            bad.append(f"via {chk!r} is not a sea chokepoint of the graph")
        pos = v.get("after_call")
        if not isinstance(pos, int) or not 0 <= pos <= len(calls) - 1:
            bad.append(f"via {chk!r}: position {pos!r} out of range")
        elif pos < last_pos:
            bad.append("via positions not ordered")
        else:
            last_pos = pos
        if isinstance(pos, int) and pos == len(calls) - 1 and not e.get("loop"):
            bad.append(f"via {chk!r}: wrap position on a non-loop service")
    for key in ("via_service_level", "via_era_default"):
        for chk in e.get(key, []):
            n = nodes.get(chk)
            if n is None or n.get("kind") != "chokepoint" or n.get("mode") != "sea":
                bad.append(f"{key} {chk!r} is not a sea chokepoint of the graph")
    if e.get("era") not in ERA_SET:
        bad.append(f"era {e.get('era')!r} not registered")
    if e.get("via_basis") not in BASES:
        bad.append(f"via_basis {e.get('via_basis')!r} not registered")
    cal = e.get("calibration") or {}
    if cal.get("basis") != "SOURCED" or not cal.get("excerpt") or not cal.get("sources"):
        bad.append("calibration must be SOURCED with sources and an excerpt")
    return bad


class Services:
    """A validated sidecar: ``entries`` (valid), ``report`` (dropped, with reasons)."""

    def __init__(self, doc: dict, file_hash: str, entries: list[dict], dropped: list[dict]):
        self.doc = doc
        self.file_sha256 = file_hash
        self.entries = entries
        self.dropped = dropped
        self.by_id = {e["service_id"]: e for e in entries}

    # ---- selection -----------------------------------------------------
    @staticmethod
    def positive_routing(e: dict) -> bool:
        flags = set(e.get("flags", []))
        if "conflicting_sources" in flags or "routing_indirect" in flags:
            return False
        return bool(e.get("via") or e.get("via_service_level") or e.get("via_era_default"))

    def select(self, eras=None, include_unverified: bool = False,
               include_unstated: bool = False) -> tuple[list[dict], list[dict]]:
        """(selected entries sorted by id, excluded [{service_id, why}])."""
        want = set(eras) if eras is not None else set(ERAS)
        sel, out = [], []
        for e in self.entries:
            why = None
            if e["era"] not in want:
                why = "era not selected"
            elif not include_unverified and "webfetch_not_machine_verified" in e.get("flags", []):
                why = "webfetch_not_machine_verified (excluded by default policy)"
            elif not include_unstated and not self.positive_routing(e):
                why = "no positive routing statement (excluded by default policy)"
            if why:
                out.append({"service_id": e["service_id"], "why": why})
            else:
                sel.append(e)
        return sel, out

    def variant_pairs(self, graph_data: dict, include_unverified: bool = False,
                      include_unstated: bool = False, min_jaccard: float = 0.75) -> list[dict]:
        """Same-loop pairs usable as ROUTING VARIANTS (typically the same
        rotation published before and after the Red Sea diversion). A pair is
        two entries of one operator family with Jaccard >= ``min_jaccard`` on
        their mapped place sets and different publication months (an analyst
        hint, MODELLED - the same rule the research step used, recomputed on
        the validated entries). ``usable`` = both entries pass the selection
        policy; ``differs`` lists the legs both rotations sail whose chokepoints
        differ (e.g. Suez in one, the Cape in the other)."""
        from .legs import build_leg_table
        ok = {e["service_id"] for e in self.select(
            include_unverified=include_unverified, include_unstated=include_unstated)[0]}
        fam = lambda e: e["operator"].split("(")[0].strip()
        out = []
        ents = self.entries
        for i, a in enumerate(ents):
            for b in ents[i + 1:]:
                if fam(a) != fam(b) or a["published"][:7] == b["published"][:7]:
                    continue
                sa, sb = set(a["calls"]), set(b["calls"])
                jac = len(sa & sb) / len(sa | sb)
                if jac < min_jaccard:
                    continue
                ta = build_leg_table([a], graph_data)["legs"]
                tb = build_leg_table([b], graph_data)["legs"]
                differs = [{"from": k[0], "to": k[1], "via_a": ta[k]["via"], "via_b": tb[k]["via"]}
                           for k in sorted(ta) if k in tb and ta[k]["via"] != tb[k]["via"]]
                out.append({"a": a["service_id"], "b": b["service_id"], "jaccard": round(jac, 4),
                            "a_era": a["era"], "b_era": b["era"],
                            "usable": a["service_id"] in ok and b["service_id"] in ok,
                            "legs_with_different_routing": differs})
        return out

    def summary(self) -> dict:
        eras: dict = {}
        for e in self.entries:
            eras[e["era"]] = eras.get(e["era"], 0) + 1
        sel, _ = self.select()
        return {
            "schema": SCHEMA,
            "file_sha256": self.file_sha256,
            "content_sha256": self.doc.get("content_sha256"),
            "graph_pin": self.doc.get("graph_pin"),
            "services_total": len(self.doc.get("services", [])),
            "services_valid": len(self.entries),
            "services_dropped": len(self.dropped),
            "dropped": self.dropped,
            "by_era": dict(sorted(eras.items())),
            "unverified_entries": sum(1 for e in self.entries
                                      if "webfetch_not_machine_verified" in e.get("flags", [])),
            "default_selection_count": len(sel),
            "policy": self.doc.get("policy"),
            "provenance": {"split": (self.doc.get("provenance") or {}).get("split"),
                           "counts": (self.doc.get("provenance") or {}).get("counts"),
                           "sources": len((self.doc.get("provenance") or {}).get("sources", {}))},
        }


def load_services(graph_data: dict, graph_hash: str | None = None,
                  path: Path | None = None) -> Services:
    """Load + validate against ``graph_data`` (raw graph dict)."""
    p = Path(path) if path else services_path()
    raw = p.read_bytes()
    try:
        doc = json.loads(raw.decode("utf-8"))
    except ValueError as exc:
        raise ServicesError(f"services file is not JSON: {exc}") from exc
    if not isinstance(doc, dict) or doc.get("schema") != SCHEMA:
        raise ServicesError(f"unsupported services schema: {doc.get('schema') if isinstance(doc, dict) else None!r}")
    if doc.get("content_sha256") != content_sha(doc):
        raise ServicesError("services content_sha256 does not match its content")
    pin = (doc.get("graph_pin") or {}).get("canonical_hash")
    if graph_hash is not None and pin != graph_hash:
        raise ServicesError("services graph_pin does not match the graph in use")
    nodes = {n["id"]: n for n in graph_data["nodes"]}
    entries, dropped, seen = [], [], set()
    for e in doc.get("services", []):
        why = validate_entry(e, nodes, seen)
        if why:
            dropped.append({"service_id": e.get("service_id"), "reasons": why})
            continue
        seen.add(e["service_id"])
        entries.append(e)
    entries.sort(key=lambda x: x["service_id"])
    return Services(doc, hashlib.sha256(raw).hexdigest(), entries, dropped)


# ---- request-body parsing (shared by the API) ---------------------------
def parse_selection(value, start_at: str) -> tuple[dict | None, str | None]:
    """Normalise a body's ``services`` value into the journaled selection
    {eras, include_unverified, include_unstated}; (None, None) when absent/false.
    Returns (selection, error)."""
    if value is None or value is False:
        return None, None
    if value is True:
        return {"eras": auto_eras(start_at), "include_unverified": False,
                "include_unstated": False}, None
    if not isinstance(value, dict):
        return None, "services must be a boolean or an object"
    extra = sorted(set(value) - {"eras", "include_unverified", "include_unstated"})
    if extra:
        return None, f"unknown services keys: {extra}"
    for k in ("include_unverified", "include_unstated"):
        if k in value and not isinstance(value[k], bool):
            return None, f"services.{k} must be a boolean"
    eras = value.get("eras")
    if eras is None:
        eras = auto_eras(start_at)
    else:
        if (not isinstance(eras, list) or not eras
                or any(not isinstance(x, str) or x not in ERA_SET for x in eras)):
            return None, f"services.eras must be a non-empty list drawn from {list(ERAS)}"
        eras = sorted(set(eras))
    return {"eras": sorted(eras), "include_unverified": bool(value.get("include_unverified", False)),
            "include_unstated": bool(value.get("include_unstated", False))}, None

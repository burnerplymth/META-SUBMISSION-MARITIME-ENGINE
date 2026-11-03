"""Port specification: a real PROFILE (data/ports_geo/profiles/
<place_id>.json) or a DEFAULT PROFILE derived from the graph place and
PortWatch daily call rates. Generic: no port-specific branches anywhere.

A PortSpec is the only thing the simulator reads. Its hash (profile_hash)
is part of every run id.
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from pathlib import Path

from .classes import (HOUR, PortSimError, berths_needed, mean_loa_kind,
                      mean_work_s, pilot_in_s, size_classes)
from .registry import (KIND_ORDER, PW_CLASS_TO_KIND, canonical, plug, sha256)

PROFILE_SCHEMA = "port-profile/1"
PROFILE_KIND_REAL = "PROFILE"
PROFILE_KIND_DEFAULT = "DEFAULT PROFILE"


@dataclass
class Group:
    id: str
    name: str
    kind: str
    berths: int
    berth_length_m: float
    basis: str

    def to_dict(self):
        return {"id": self.id, "name": self.name, "kind": self.kind,
                "berths": self.berths,
                "berth_length_m": round(self.berth_length_m, 3),
                "basis": self.basis}


@dataclass
class PortSpec:
    place_id: str
    name: str
    tier: str
    profile_kind: str
    profile_source: str | None
    geometry_hash: str | None
    groups: list
    anchorage_slots: int
    anchorage_basis: str
    channel_present: bool
    channel_basis: str
    yard_capacity_teu: float | None
    yard_basis: str
    calls_per_day: dict
    work_per_call: dict
    work_basis: dict
    teu_per_day: float
    loa_scale: dict
    place_capacity: float | None
    place_capacity_unit: str | None
    notes: list = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "place_id": self.place_id, "name": self.name, "tier": self.tier,
            "profile_kind": self.profile_kind,
            "profile_source": self.profile_source,
            "geometry_hash": self.geometry_hash,
            "groups": [g.to_dict() for g in self.groups],
            "anchorage": {"slots": self.anchorage_slots,
                          "basis": self.anchorage_basis},
            "channel": {"present": self.channel_present,
                        "basis": self.channel_basis},
            "yard": {"capacity_teu": (None if self.yard_capacity_teu is None
                                      else round(self.yard_capacity_teu, 3)),
                     "basis": self.yard_basis},
            "calls_per_day": {k: round(v, 6) for k, v in
                              sorted(self.calls_per_day.items())},
            "work_per_call": {k: round(v, 3) for k, v in
                              sorted(self.work_per_call.items())},
            "work_basis": dict(sorted(self.work_basis.items())),
            "teu_per_day": round(self.teu_per_day, 3),
            "loa_scale_by_kind": {k: round(v, 6) for k, v in
                                  sorted(self.loa_scale.items())},
            "place_capacity": self.place_capacity,
            "place_capacity_unit": self.place_capacity_unit,
            "notes": list(self.notes)}

    @property
    def profile_hash(self) -> str:
        return sha256(canonical(self.to_dict()))


# ------------------------------------------------------------------ sources
_CACHE: dict = {}


def _load_json_cached(path: Path):
    try:
        st = path.stat()
    except OSError:
        return None
    key = (str(path), st.st_mtime_ns, st.st_size)
    if key in _CACHE:
        return _CACHE[key]
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    for k in [k for k in _CACHE if k[0] == str(path)]:
        del _CACHE[k]
    _CACHE[key] = doc
    return doc


def public_profile_path(path) -> str:
    """The served form of a profile file path: repo-relative (POSIX) when
    the file lies inside the repository, else just its file name. Never an
    absolute server path, and the profile hash (hence the run id) no longer
    depends on where the checkout lives."""
    p = Path(str(path))
    try:
        return p.resolve().relative_to(_REPO_ROOT).as_posix()
    except (ValueError, OSError):
        return p.name


_REPO_ROOT = Path(__file__).resolve().parents[2]


@dataclass
class Sources:
    graph_path: Path
    snapshots_dir: Path
    profiles_dir: Path | None = None

    def node(self, place_id: str):
        doc = _load_json_cached(Path(self.graph_path))
        if not isinstance(doc, dict):
            raise PortSimError("places_unavailable", "place data unreadable", 503)
        idx = _CACHE.get(("idx", str(self.graph_path)))
        stamp = id(doc)
        if idx is None or idx[0] != stamp:
            idx = (stamp, {n["id"]: n for n in doc.get("nodes", [])
                           if isinstance(n, dict) and n.get("id")})
            _CACHE[("idx", str(self.graph_path))] = idx
        return idx[1].get(place_id)

    def portwatch(self, place_id: str):
        doc = _load_json_cached(Path(self.snapshots_dir) / "portwatch_daily.json")
        if not isinstance(doc, dict):
            return None
        idx = _CACHE.get(("pw", str(self.snapshots_dir)))
        stamp = id(doc)
        if idx is None or idx[0] != stamp:
            m = {}
            for r in doc.get("records", []):
                if (isinstance(r, dict) and r.get("record_type") == "port_goods"
                        and r.get("node_id")):
                    m[r["node_id"]] = r
            idx = (stamp, m)
            _CACHE[("pw", str(self.snapshots_dir))] = idx
        return idx[1].get(place_id)

    def profile_file(self, place_id: str):
        if self.profiles_dir is None:
            return None, None
        p = Path(self.profiles_dir) / f"{place_id}.json"
        if not p.exists():
            return None, None
        try:
            doc = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None, public_profile_path(p)
        return (doc if isinstance(doc, dict) else None), public_profile_path(p)

    def port_ids(self):
        doc = _load_json_cached(Path(self.graph_path)) or {}
        return sorted(n["id"] for n in doc.get("nodes", [])
                      if isinstance(n, dict) and n.get("kind") == "port"
                      and n.get("mode", "sea") == "sea")

    def profile_ids(self):
        if self.profiles_dir is None or not Path(self.profiles_dir).is_dir():
            return []
        return sorted(p.stem for p in Path(self.profiles_dir).glob("*.json"))


# ---------------------------------------------------------------- derivation
def _num(x, default=None):
    if isinstance(x, bool) or not isinstance(x, (int, float)):
        return default
    if not math.isfinite(x):
        return default
    return float(x)


def _derive_calls_and_work(node, pw, notes):
    """calls/day and work per call by kind from PortWatch + graph."""
    psi = float(plug("teu_per_call_fallback"))
    lo, hi = plug("teu_per_call_bounds")
    cap = _num(node.get("capacity"))
    unit = str(node.get("capacity_unit", ""))
    cap_teu = cap if (cap is not None and unit.upper().startswith("TEU")) else None
    calls: dict = {}
    work: dict = {}
    basis: dict = {}
    if pw is not None:
        pcd = pw.get("portcalls_per_day") or {}
        imp = pw.get("import_tonnes_per_day") or {}
        exp = pw.get("export_tonnes_per_day") or {}
        for pwc, kind in PW_CLASS_TO_KIND.items():
            lam = _num(pcd.get(pwc), 0.0)
            if lam and lam > 0:
                calls[kind] = lam
        for kind in calls:
            pwc = [k for k, v in PW_CLASS_TO_KIND.items() if v == kind][0]
            if kind == "container":
                continue
            t = (_num(imp.get(pwc), 0.0) or 0.0) + (_num(exp.get(pwc), 0.0) or 0.0)
            if t > 0:
                work[kind] = t / calls[kind]
                basis[kind] = ("PortWatch AIS tonnage estimate (import+export "
                               "tonnes/day) / PortWatch calls/day (MODELLED)")
            else:
                work[kind] = float(plug("default_tonnes_per_call")[kind])
                basis[kind] = "default tonnes per call (TEMPORARY)"
        src = "PortWatch daily layers (30-day mean, AIS estimate)"
    if not calls:
        # no PortWatch record (or all zero): the chain engine's own unit,
        # vessel-calls/day = TEU/day / teu_per_call, all container
        if cap_teu is not None and cap_teu > 0:
            calls = {"container": cap_teu / psi}
            notes.append("no PortWatch call rates for this place: arrival rate "
                         "= place capacity / teu_per_call (engine2 lambda0), "
                         "all container (DEFAULT)")
        else:
            calls = {"container": 1.0}
            notes.append("no PortWatch call rates and no TEU capacity: 1 "
                         "container call/day assumed (DEFAULT, TEMPORARY)")
        src = "DEFAULT (engine2 lambda0)"
    if "container" in calls:
        tpc = psi
        b = "teu_per_call fallback (engine2 CAL median)"
        if cap_teu is not None and cap_teu > 0:
            cand = cap_teu / calls["container"]
            if lo <= cand <= hi:
                tpc, b = cand, ("place capacity TEU/day / container calls/day "
                                "(the baseline throughput the chain engine uses)")
            else:
                notes.append(f"place capacity / container calls = {cand:.0f} "
                             f"TEU/call is outside [{lo:.0f}, {hi:.0f}]: "
                             f"fallback {psi:.0f} used")
        work["container"], basis["container"] = tpc, b
    return calls, work, basis, src


def _groups_from_profile(prof, calls, work, notes):
    out = []
    raw = prof.get("berth_groups")
    if not isinstance(raw, list):
        return out
    for i, g in enumerate(raw):
        if not isinstance(g, dict):
            continue
        kind = g.get("kind") if g.get("kind") in KIND_ORDER + ("unknown",) else "unknown"
        n = _num(g.get("berth_count_est"))
        berths = int(n) if (n is not None and n >= 1) else 1
        berths = min(berths, int(plug("berth_geometry")["berth_count_cap"]))
        q = _num(g.get("quay_length_m"))
        if q is not None and q > 0:
            length = q / berths
            lb = "quay_length_m / berth_count_est"
        else:
            length = None
            lb = "default"
        out.append(Group(id=str(g.get("id") or f"g{i}"),
                         name=str(g.get("name") or g.get("id") or f"group {i}"),
                         kind=kind, berths=berths,
                         berth_length_m=length if length else 0.0,
                         basis=f"profile ({g.get('basis') or 'no basis given'}); "
                               f"length: {lb}"))
    return out


def route_groups(groups, kind):
    """Group indices a kind may use: exact kind, else general/unknown, else
    every group. Deterministic order."""
    exact = [i for i, g in enumerate(groups) if g.kind == kind]
    if exact:
        return exact
    soft = [i for i, g in enumerate(groups) if g.kind in ("general", "unknown")]
    return soft or list(range(len(groups)))


def _default_groups(calls, work, channel_present, mu):
    out = []
    margin = float(plug("berth_geometry")["default_length_margin"])
    cap = int(plug("berth_geometry")["berth_count_cap"])
    pin = pilot_in_s(channel_present)
    for kind in KIND_ORDER:
        lam = calls.get(kind, 0.0)
        if lam <= 0:
            continue
        bt_d = (pin + mean_work_s(kind, work[kind])) / 86400.0
        n = max(1, min(cap, math.ceil(mu * lam * bt_d)))
        maxloa = max(c["loa_m"] for c in size_classes(kind, 1.0))
        out.append(Group(id=f"{kind}-1", name=f"{kind} berths (DEFAULT)",
                         kind=kind, berths=n,
                         berth_length_m=margin * maxloa,
                         basis="DEFAULT: berths = ceil(mu_max_ratio x calls/day x "
                               "mean berth time)"))
    return out


def _fill_lengths(groups):
    margin = float(plug("berth_geometry")["default_length_margin"])
    for g in groups:
        if g.berth_length_m <= 0:
            kinds = [g.kind] if g.kind in KIND_ORDER else list(KIND_ORDER)
            maxloa = max(c["loa_m"] for k in kinds for c in size_classes(k, 1.0))
            g.berth_length_m = margin * maxloa
            g.basis += f" -> default length {g.berth_length_m:.0f} m"


def build_spec(place_id: str, src: Sources) -> PortSpec:
    node = src.node(place_id)
    if node is None:
        raise PortSimError("unknown_place", f"unknown place_id: {place_id!r}", 404)
    if node.get("kind") != "port" or node.get("mode", "sea") != "sea":
        raise PortSimError("not_a_port",
                           f"{place_id!r} is a {node.get('kind')} place; the "
                           "port twin simulates sea ports only")
    mu = float(plug("mu_max_ratio"))
    notes: list = []
    pw = src.portwatch(place_id)
    calls, work, work_basis, call_src = _derive_calls_and_work(node, pw, notes)
    notes.append(f"arrival rate source: {call_src}")

    prof, prof_path = src.profile_file(place_id)
    real = prof is not None and prof.get("schema") == PROFILE_SCHEMA
    if prof_path is not None and not real:
        notes.append("profile file present but unreadable or wrong schema: "
                     "DEFAULT PROFILE used")
    if real and prof.get("place_id") not in (None, place_id):
        notes.append("profile place_id mismatch: DEFAULT PROFILE used")
        real = False

    # channel
    channel_present, channel_basis = True, "DEFAULT: an approach channel is assumed (TEMPORARY)"
    if real and isinstance(prof.get("channel"), dict) and isinstance(
            prof["channel"].get("present"), bool):
        channel_present = prof["channel"]["present"]
        channel_basis = "profile.channel.present"

    # groups
    groups = _groups_from_profile(prof, calls, work, notes) if real else []
    if not groups:
        if real:
            notes.append("profile has no usable berth groups: DEFAULT berth "
                         "groups derived from call rates")
        groups = _default_groups(calls, work, channel_present, mu)
    _fill_lengths(groups)

    # LOA scale per kind from the profile's mean LOA of that kind (the same
    # figure the port geometry builder divided the quay length by to count berths of
    # that kind: one berth = one vessel of the kind's mean length). The
    # profile's single `mean_loa_m` is the CONTAINER value, so it is compared
    # with the container classes only, never with the call-weighted mean of
    # all kinds (that mismatch inflated tankers, bulkers and ro-ros by the
    # container/mix ratio, pre-1.1).
    loa_scale: dict = {}
    if real and isinstance(prof.get("params"), dict):
        pr = prof["params"]
        by = pr.get("mean_loa_m_by_kind")
        by = by if isinstance(by, dict) else {}
        lo, hi = plug("berth_geometry")["loa_scale_bounds"]
        for k in KIND_ORDER:
            if calls.get(k, 0.0) <= 0:
                continue
            m = _num(by.get(k))
            if m is None and k == "container":
                m = _num(pr.get("mean_loa_m"))
            if not m or m <= 0:
                continue
            ml = mean_loa_kind(k)
            if ml > 0 and lo <= m / ml <= hi:
                loa_scale[k] = m / ml
                notes.append(f"{k} vessel LOAs scaled x{m / ml:.3f} to the "
                             f"profile mean LOA {m:.0f} m of that kind")
            else:
                notes.append(f"profile mean LOA {m:.0f} m for {k} is outside "
                             f"x[{lo}, {hi}] of the class mix: ignored")
    # anchorage
    slots, a_basis = None, None
    if real and isinstance(prof.get("anchorage"), dict):
        c = _num(prof["anchorage"].get("capacity_vessels_est"))
        if c is not None and c >= 1:
            slots = int(c)
            a_basis = "profile.anchorage.capacity_vessels_est"
    if slots is None:
        qc = _num(node.get("queue_cap"))
        psi = float(plug("teu_per_call_fallback"))
        ad = plug("anchorage_default")
        if qc is not None and str(node.get("capacity_unit", "")).upper().startswith("TEU"):
            slots = math.ceil(qc / psi)
            a_basis = "DEFAULT: ceil(place queue_cap / teu_per_call), the chain engine's soft anchorage limit"
        else:
            slots = ad["min_slots"]
            a_basis = "DEFAULT: minimum slots (no queue_cap in TEU)"
        slots = max(ad["min_slots"], min(ad["max_slots"], slots))

    # baseline TEU/day
    teu_day = calls.get("container", 0.0) * work.get("container", 0.0)

    # yard
    yard_cap, y_basis = None, "no container calls: no yard model"
    if "container" in calls:
        y = plug("yard")
        y0 = y["import_share"] * teu_day * y["dwell_days"]
        prof_cap = None
        if real and isinstance(prof.get("yard"), dict):
            blocks = prof["yard"].get("blocks")
            if isinstance(blocks, list):
                vals = [_num(b.get("capacity_teu_est")) for b in blocks
                        if isinstance(b, dict)]
                vals = [v for v in vals if v is not None and v > 0]
                if vals:
                    prof_cap = sum(vals)
            if prof_cap is None:
                area = _num(prof["yard"].get("total_area_m2"))
                m2 = _num((prof.get("params") or {}).get("m2_per_teu"))
                if area and m2 and area > 0 and m2 > 0:
                    prof_cap = area / m2
        if prof_cap is not None and y0 > 0:
            f0 = y0 / prof_cap
            lo, hi = y["profile_fill_range"]
            if lo <= f0 <= hi:
                yard_cap, y_basis = prof_cap, (
                    f"profile yard capacity (baseline fill {f0:.2f})")
            else:
                notes.append(f"profile yard capacity {prof_cap:.0f} TEU gives "
                             f"baseline fill {f0:.2f} outside [{lo}, {hi}]: "
                             "yard anchored to target fill instead")
        if yard_cap is None:
            yard_cap = y0 / y["target_fill"] if y0 > 0 else None
            if yard_cap is not None and not y_basis.startswith("profile"):
                y_basis = ("DEFAULT: capacity set so baseline fill = target_fill "
                           "(TEMPORARY)")

    return PortSpec(
        place_id=place_id, name=str(node.get("name") or place_id),
        tier=str(node.get("tier") or "core"),
        profile_kind=PROFILE_KIND_REAL if real else PROFILE_KIND_DEFAULT,
        profile_source=prof_path if real else None,
        geometry_hash=(prof.get("geometry_hash") if real else None),
        groups=groups, anchorage_slots=int(slots), anchorage_basis=a_basis,
        channel_present=channel_present, channel_basis=channel_basis,
        yard_capacity_teu=yard_cap, yard_basis=y_basis,
        calls_per_day=calls, work_per_call=work, work_basis=work_basis,
        teu_per_day=teu_day, loa_scale=loa_scale,
        place_capacity=_num(node.get("capacity")),
        place_capacity_unit=node.get("capacity_unit"),
        notes=notes)


# ------------------------------------------------------------------- anchor
def class_berths(spec: PortSpec, kind: str):
    """For each size class of `kind` (registry order): berths held in each
    routed group {group index: berths}, from the LOA after the profile
    scale and the group's berth length."""
    rs = route_groups(spec.groups, kind)
    cl = size_classes(kind, spec.work_per_call.get(kind, 1.0),
                      spec.loa_scale.get(kind, 1.0))
    return [{gi: berths_needed(c["loa_m"], spec.groups[gi].berth_length_m,
                               spec.groups[gi].berths) for gi in rs}
            for c in cl]


def anchor_scales(spec: PortSpec, anchor: str):
    """Per-kind service-time scale s_k (work duration multiplier) and a
    report. anchor='structural' returns 1.0 for every kind and reports the
    implied baseline berth utilisation instead.

    A call holds `n` berths of its group (n = 1 when the vessel is
    no longer than one berth, which is every call of a DEFAULT PROFILE), so
    berth capacity is counted in berth-time: the anchor makes the mean
    berth-time per call equal to (effective berths) / (mu_max_ratio x
    calls/day), and the utilisation reported is berth-time over capacity."""
    mu = float(plug("mu_max_ratio"))
    pin = pilot_in_s(spec.channel_present)
    routes = {k: route_groups(spec.groups, k) for k in KIND_ORDER
              if spec.calls_per_day.get(k, 0.0) > 0}

    def berth_time(k, wts):
        """(berths held per call, berth-seconds of work per call) of a kind
        with the groups weighted by `wts` {group: weight}."""
        cl = size_classes(k, spec.work_per_call[k], spec.loa_scale.get(k, 1.0))
        nb = class_berths(spec, k)
        tot = sum(wts.values())
        multi = any(n > 1 for row in nb for n in row.values())
        if multi and tot > 0:
            nbar = [sum(wts[gi] * row[gi] for gi in row) / tot for row in nb]
            return (sum(c["share"] * n for c, n in zip(cl, nbar)),
                    sum(c["share"] * n * c["work"] / c["rate_per_h"] * HOUR
                        for c, n in zip(cl, nbar)))
        return 1.0, mean_work_s(k, spec.work_per_call[k])

    # A group several kinds may use (profile groups of kind `unknown` or
    # `general`, or a kind with no group of its own) is shared between
    # them in proportion to the berth-time each kind offers it at the cited
    # (nominal) service times. Before 1.1 the split was equal per kind,
    # which starves a heavy kind (28 container calls/day got the same share
    # as 2 bulk calls/day) and left the sum of baseline utilisations over
    # the shared berths above 1/mu_max_ratio once a scale bound bit.
    nominal = {}
    for k, rs in routes.items():
        n_k, w_k = berth_time(k, {gi: float(spec.groups[gi].berths) for gi in rs})
        nominal[k] = spec.calls_per_day[k] * (pin * n_k + w_k)
    gload: dict = {}
    for k, rs in routes.items():
        for gi in rs:
            gload[gi] = gload.get(gi, 0.0) + nominal[k]
    scales, rep = {}, {}
    for k in KIND_ORDER:
        lam = spec.calls_per_day.get(k, 0.0)
        if lam <= 0:
            continue
        wts = {gi: spec.groups[gi].berths * (nominal[k] / gload[gi] if gload[gi] > 0 else 1.0)
               for gi in routes[k]}
        b_eff = sum(wts.values())
        swork = mean_work_s(k, spec.work_per_call[k])
        n_mean, w_n = berth_time(k, wts)
        nominal_berth_s = pin * n_mean + w_n
        implied = lam / 86400.0 * nominal_berth_s / b_eff if b_eff > 0 else float("inf")
        row = {"calls_per_day": lam, "berths_effective": round(b_eff, 3),
               "mean_work_h_nominal": round(swork / 3600.0, 3),
               "mean_berths_per_call": round(n_mean, 4),
               "implied_utilisation_structural": round(implied, 4)}
        if anchor == "engine2" and b_eff > 0:
            allowed = b_eff / (mu * lam / 86400.0)
            s = (allowed - pin * n_mean) / w_n if w_n > 0 else 1.0
            row["scale_raw"] = round(s, 4)
            capped = min(8.0, max(0.2, s))
            row["scale_capped"] = capped != s
            s = capped
        else:
            s = 1.0
        scales[k] = s
        row["scale"] = round(s, 4)
        row["implied_utilisation"] = round(
            lam / 86400.0 * (pin * n_mean + s * w_n) / b_eff if b_eff > 0 else float("inf"), 4)
        rep[k] = row
    return scales, rep

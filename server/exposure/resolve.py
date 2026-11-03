"""Free-text place resolution for the shipment list (read-only, deterministic).

A shipment names its origin and destination as text ("Shanghai", "Port of Rotterdam", "NLRTM",
"Los Angeles, US"). This module maps the text to a place id of the served graph using the graph's own
names, aliases and codes. Rules:

* the pool is chosen by the shipment's mode (sea: ports; air: air hubs; ground: ground nodes); chokepoints
  are never an origin or a destination, and a ``via`` text may name any sea place;
* an exact id, name, code or alias match resolves; two places on the same key are ambiguous;
* a close spelling resolves only when it is clearly the best (score >= 0.90 and a margin over the next
  place) and is reported as ``fuzzy``; anything else is unresolved with ranked suggestions;
* a text is never resolved by a guess that is not reported: the caller returns ``match_quality`` for every
  resolved place and the suggestions for every unresolved one.
"""
from __future__ import annotations

import difflib
import re
import unicodedata

FUZZY_ACCEPT = 0.90
FUZZY_MARGIN = 0.06
FUZZY_SUGGEST = 0.70
MAX_SUGGESTIONS = 5

_STRIP = re.compile(r"[^a-z0-9 ]+")
_CODE = re.compile(r"^[A-Za-z]{2}[A-Za-z0-9]{3}$")
_NOISE_PREFIX = ("port of ", "port de ", "porto de ", "puerto de ", "the ")
_NOISE_SUFFIX = (" port", " harbour", " harbor", " container terminal", " terminal", " seaport")


def norm(text: str) -> str:
    """Lower case, accents removed, punctuation to spaces, single spaces."""
    t = unicodedata.normalize("NFKD", str(text))
    t = "".join(c for c in t if not unicodedata.combining(c)).casefold()
    t = t.replace("&", " and ")
    t = _STRIP.sub(" ", t)
    return " ".join(t.split())


def _variants(text: str) -> list[str]:
    """The normalised text plus the same text without a leading or trailing generic word."""
    n = norm(text)
    out = [n] if n else []
    cur = n
    for p in _NOISE_PREFIX:
        if cur.startswith(p) and len(cur) > len(p):
            cur = cur[len(p):]
    for s in _NOISE_SUFFIX:
        if cur.endswith(s) and len(cur) > len(s):
            cur = cur[: -len(s)]
    if cur and cur not in out:
        out.append(cur)
    return out


def _initials(country: str) -> str:
    return "".join(w[0] for w in norm(country).split())


class PlaceIndex:
    """Name index over a graph's places. Built once per graph."""

    def __init__(self, graph):
        self.nodes = graph.nodes
        self.by_id = {pid: pid for pid in graph.nodes}
        self.by_id_norm = {norm(pid): pid for pid in graph.nodes}
        self.name_keys: dict[str, set] = {}
        self.code_keys: dict[str, set] = {}
        self.alias_keys: dict[str, set] = {}
        self.fuzzy_keys: list[tuple] = []          # (key, place id)
        for pid, n in graph.nodes.items():
            names = [str(n.get("name", ""))]
            for k in _variants(names[0]):
                self.name_keys.setdefault(k, set()).add(pid)
                self.fuzzy_keys.append((k, pid))
            for a in n.get("aliases") or []:
                a = str(a)
                if _CODE.match(a) and a.upper() == a:
                    self.code_keys.setdefault(a.casefold(), set()).add(pid)
                    continue
                for k in _variants(a):
                    self.alias_keys.setdefault(k, set()).add(pid)
                    self.fuzzy_keys.append((k, pid))
        self._memo: dict = {}

    # ---------------------------------------------------------------- pools
    def in_pool(self, pid: str, pool: str) -> bool:
        n = self.nodes[pid]
        kind = n.get("kind")
        if pool == "via":
            return n.get("mode") == "sea"
        if kind == "chokepoint":
            return False
        if pool == "place":
            return True
        return n.get("mode") == pool

    def row(self, pid: str) -> dict:
        n = self.nodes[pid]
        return {"id": pid, "name": n.get("name"), "country": n.get("country"), "mode": n.get("mode"),
                "kind": n.get("kind"), "tier": n.get("tier", "core"),
                "lat": n.get("lat"), "lon": n.get("lon")}

    def _hint_filter(self, ids: set, hint: str, strict: bool = False) -> set:
        h = norm(hint)
        if not h:
            return ids
        keep = set()
        for pid in ids:
            c = norm(self.nodes[pid].get("country") or "")
            if c and (c == h or c.startswith(h) or _initials(c) == h.replace(" ", "")):
                keep.add(pid)
        return keep if strict else (keep or ids)

    # -------------------------------------------------------------- resolve
    def resolve(self, text: str, pool: str) -> dict:
        """{'status': 'resolved', 'id', 'quality'} or {'status': 'unresolved', 'suggestions': [...]}."""
        key = (pool, text)
        hit = self._memo.get(key)
        if hit is not None:
            return hit
        out = self._resolve(text, pool)
        if len(self._memo) > 4096:
            self._memo.clear()
        self._memo[key] = out
        return out

    def _resolve(self, text: str, pool: str) -> dict:
        raw = str(text).strip()
        if not raw:
            return {"status": "unresolved", "suggestions": []}
        # exact place id
        if raw in self.by_id and self.in_pool(raw, pool):
            return {"status": "resolved", "id": raw, "quality": "id"}
        if raw in self.by_id and pool == "place":
            return {"status": "resolved", "id": raw, "quality": "id"}
        hint = ""
        main = raw
        if "," in raw:
            main, _, hint = raw.partition(",")
        attempts = [(raw, "", False)]
        if hint:
            attempts.append((main, hint, False))
        toks = norm(raw).split()
        for i in range(len(toks) - 1, 0, -1):                 # "Hamburg Germany": a trailing country
            attempts.append((" ".join(toks[:i]), " ".join(toks[i:]), True))
        for cand_text, cand_hint, strict in attempts:
            for level, table, quality in (("name", self.name_keys, "exact"),
                                          ("code", self.code_keys, "code"),
                                          ("alias", self.alias_keys, "alias")):
                ids = set()
                keys = [cand_text.strip().casefold()] if level == "code" else _variants(cand_text)
                for k in keys:
                    ids |= {p for p in table.get(k, ()) if self.in_pool(p, pool)}
                    if ids:
                        break
                if cand_hint:
                    ids = self._hint_filter(ids, cand_hint, strict)
                if len(ids) == 1:
                    return {"status": "resolved", "id": next(iter(ids)), "quality": quality}
                if len(ids) > 1:
                    return {"status": "unresolved", "reason": "ambiguous",
                            "suggestions": [self.row(p) for p in self._rank(sorted(ids))][:MAX_SUGGESTIONS]}
        target = main if hint else raw
        for q in _variants(target):
            if len(q) < 4:
                continue
            pref = {pid for key, pid in self.fuzzy_keys if key.startswith(q + " ") and self.in_pool(pid, pool)}
            if len(pref) == 1:
                return {"status": "resolved", "id": next(iter(pref)), "quality": "prefix"}
        return self._fuzzy(target, pool)

    def _rank(self, ids):
        return sorted(ids, key=lambda p: (0 if self.nodes[p].get("tier", "core") == "core" else 1, str(self.nodes[p].get("name")), p))

    def _fuzzy(self, text: str, pool: str) -> dict:
        qs = _variants(text)
        if not qs:
            return {"status": "unresolved", "suggestions": []}
        best: dict[str, float] = {}
        for q in qs:
            qt = q.split()
            for key, pid in self.fuzzy_keys:
                if not self.in_pool(pid, pool):
                    continue
                s = difflib.SequenceMatcher(None, q, key).ratio()
                # containment: the query is a whole-word prefix of a name, or a name of a whole word of the query
                if len(q) >= 3 and (key.startswith(q + " ") or (" " + q + " ") in (" " + key + " ")):
                    s = max(s, 0.80)
                kt = key.split()
                if len(qt) > 1 and all(t in kt for t in qt):
                    s = max(s, 0.80)
                if s > best.get(pid, 0.0):
                    best[pid] = s
        if not best:
            return {"status": "unresolved", "suggestions": []}
        ranked = sorted(best.items(), key=lambda kv: (-(kv[1] + (0.02 if self.nodes[kv[0]].get("tier", "core") == "core" else 0.0)), kv[0]))
        top_id, top = ranked[0]
        second = ranked[1][1] if len(ranked) > 1 else 0.0
        if top >= FUZZY_ACCEPT and top - second >= FUZZY_MARGIN:
            return {"status": "resolved", "id": top_id, "quality": "fuzzy", "score": round(top, 3)}
        sugg = [self.row(p) for p, s in ranked if s >= FUZZY_SUGGEST][:MAX_SUGGESTIONS]
        return {"status": "unresolved", "reason": "no_match" if not sugg else "unclear", "suggestions": sugg}

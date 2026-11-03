"""Graph-node index for grounding feed records to graph node ids.

Node list source: ``data/graph/graph.json`` in this repo (the served graph is
992 nodes / 4,550 edges). Override with env ``SUPERMAP_NODES_FILE`` — read at
CALL time in :func:`_candidates`, not frozen at import
time, and with no fallback into a home-directory workspace path
(the old prep-census fallback under the user's home tree is gone).
"""
from __future__ import annotations

import json
import math
import os
import re
from pathlib import Path

from .base import REPO_ROOT


def _candidates() -> list:
    return [
        os.environ.get("SUPERMAP_NODES_FILE"),
        str(REPO_ROOT / "data" / "graph" / "graph.json"),
    ]


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def _tokens(s: str) -> tuple[str, ...]:
    """Word tokens of a name/phrase or a free-text blob: lowercase
    alphanumeric runs. Matching on token sequences gives word
    boundaries for free ('bog' cannot match inside 'bogota', and a
    one-word name cannot match a longer unrelated word)."""
    return tuple(re.findall(r"[a-z0-9]+", (s or "").lower()))


# IATA (3) / ICAO (4) / UN-LOCODE (5) codes as they appear in node
# aliases: single all-caps alphanumeric tokens. They are CODE tokens,
# not names — they are excluded from the name index entirely (e.g.
# the alias 'BOG' for Bogotá grounded the English word "bog" in prose).
_CODE_ALIAS_RE = re.compile(r"^[A-Z][A-Z0-9]{2,4}$")


def _is_code_alias(label: str) -> bool:
    label = (label or "").strip()
    return " " not in label and bool(_CODE_ALIAS_RE.match(label))


# Single-token free-text matches require token length >= 5 AND the
# token not being a common English word. This guard is deliberately a
# SMALL explicit list of the observed failure words, not a
# general English dictionary:
#   "mobile" — "residents in mobile homes" grounded to the Port of
#              Mobile (sea_mobile), whose entire name is this one
#              ambiguous word;
#   "bog"    — "cranberry bog" grounded to Bogotá (air_bogota) via
#              its IATA alias; also below the length floor, listed
#              here so the failure stays guarded if the floor moves.
# Structured lookups (by_name on a complete name field, by_locode)
# are NOT subject to this guard — it governs free-text matching only.
COMMON_WORD_GUARD = {"mobile", "bog"}
MIN_SINGLE_TOKEN_LEN = 5


class NodeIndex:
    def __init__(self, nodes: list[dict]):
        self.nodes = {n["id"]: n for n in nodes}
        self._by_locode: dict[str, str] = {}
        self._by_name: dict[str, str] = {}
        # token-tuple phrases for free-text matching, longest first so a
        # multi-word name wins over any single-word name it contains.
        self._phrases: list[tuple[tuple[str, ...], str]] = []
        for n in nodes:
            code = ((n.get("codes") or {}).get("unlocode") or "").upper()
            if code:
                self._by_locode[code] = n["id"]
            for label in [n.get("name", "")] + list(n.get("aliases") or []):
                if _is_code_alias(label):
                    continue  # codes live in by_locode / codes, not names
                toks = _tokens(label)
                if not toks:
                    continue
                self._by_name[" ".join(toks)] = n["id"]
        # phrases derive from the final name map so match_text and
        # by_name always agree on which node a given phrase means.
        self._phrases = [(tuple(k.split()), v)
                         for k, v in self._by_name.items()]
        self._phrases.sort(key=lambda p: -len(p[0]))

    @classmethod
    def load(cls) -> "NodeIndex":
        candidates = _candidates()
        for cand in candidates:
            if not cand:
                continue
            p = Path(cand).expanduser()
            if p.exists():
                data = json.loads(p.read_text(encoding="utf-8"))
                nodes = data["nodes"] if isinstance(data, dict) else data
                # prep draft uses `type`; graph nodes use `kind` — both fine here.
                return cls(nodes)
        raise FileNotFoundError(f"no node list found in {candidates}")

    def __contains__(self, node_id: str) -> bool:
        return node_id in self.nodes

    def by_locode(self, locode: str) -> str | None:
        return self._by_locode.get((locode or "").upper())

    def by_name(self, name: str) -> str | None:
        """Exact PHRASE lookup for structured name fields (e.g. a
        PortWatch portname): the whole input must equal a node name or
        non-code alias, token for token. Code tokens (IATA/ICAO/
        LOCODE aliases) never resolve here — use by_locode for codes."""
        return self._by_name.get(" ".join(_tokens(name)))

    def match_text(self, text: str) -> str | None:
        """Ground free text (e.g. an NWS headline/areaDesc) to a node:
        name/alias PHRASES matched as contiguous token sequences
        (word boundaries enforced). Multi-token phrases match as
        phrases; a single-token match additionally requires
        length >= MIN_SINGLE_TOKEN_LEN and absence from
        COMMON_WORD_GUARD. The earliest match in the text wins;
        ties go to the longest phrase."""
        toks = _tokens(text)
        if not toks:
            return None
        # first occurrence index of every window of each phrase length
        first_at: dict[tuple[str, ...], int] = {}
        for size in {len(p) for p, _ in self._phrases}:
            for i in range(len(toks) - size + 1):
                first_at.setdefault(toks[i:i + size], i)
        best: tuple[int, int, str] | None = None  # (position, -len, id)
        for phrase, node_id in self._phrases:
            size = len(phrase)
            if size == 1:
                token = phrase[0]
                if len(token) < MIN_SINGLE_TOKEN_LEN \
                        or token in COMMON_WORD_GUARD:
                    continue
            pos = first_at.get(phrase)
            if pos is None:
                continue
            cand = (pos, -size, node_id)
            if best is None or cand[:2] < best[:2]:
                best = cand
        return best[2] if best else None

    @staticmethod
    def _haversine_km(lat1, lon1, lat2, lon2) -> float:
        r = 6371.0
        p1, p2 = math.radians(lat1), math.radians(lat2)
        dp = math.radians(lat2 - lat1)
        dl = math.radians(lon2 - lon1)
        a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
        return 2 * r * math.asin(math.sqrt(a))

    def nearest(self, lat: float, lon: float, *, max_km: float,
                prefixes: tuple[str, ...] | None = None) -> tuple[str, float] | None:
        """Nearest node id within max_km, optionally restricted by id prefix."""
        best, best_d = None, max_km
        for n in self.nodes.values():
            if prefixes and not n["id"].startswith(prefixes):
                continue
            if n.get("lat") is None or n.get("lon") is None:
                continue
            d = self._haversine_km(lat, lon, n["lat"], n["lon"])
            if d <= best_d:
                best, best_d = n["id"], d
        return (best, best_d) if best else None

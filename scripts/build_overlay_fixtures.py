#!/usr/bin/env python3
"""Build the overlay-contract example fixtures for the UI.

Reads ONLY the baked scan pack (data/snapshots/satwatch) and the recorded
reference detections, replays them through the OFFLINE
reference-counts detector, and writes one ``satwatch-overlay/1`` envelope per
anchorage to server/satwatch/fixtures/overlay/<node_id>.json.

Honesty: the recorded detections are approximate POINTS. Their ``mask``
and ``bbox`` are null in the fixtures because no mask or measured box
was ever produced - nothing is invented to make the layer look richer.
Every item says ``detector_id: reference-counts`` and the envelope's
provider_label is "REFERENCE COUNTS - OFFLINE". A real SAM scan
(Meta API or open-weights worker) fills the same fields with polygon /
RLE masks and scores.

    .venv/bin/python scripts/build_overlay_fixtures.py
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from server.satwatch import overlay, replay  # noqa: E402
from server.satwatch.detect import ReferenceCountsDetector  # noqa: E402

OUT = REPO / "server" / "satwatch" / "fixtures" / "overlay"


def build(out_dir: Path = OUT) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    written = []
    with tempfile.TemporaryDirectory() as tmp:
        records = replay.replay_pack(replay.DEFAULT_SRC_DIR, Path(tmp),
                                     detector=ReferenceCountsDetector())
        for rec in sorted(records, key=lambda r: r["node_id"]):
            env = overlay.overlay_for_scan(rec)
            path = out_dir / f"{rec['node_id']}.json"
            path.write_text(json.dumps(env, indent=1, sort_keys=True) + "\n",
                            encoding="utf-8")
            written.append(path)
    return written


if __name__ == "__main__":
    for p in build():
        print(p.relative_to(REPO))

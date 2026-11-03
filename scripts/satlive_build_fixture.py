#!/usr/bin/env python3
"""Build one shipped aerial demo fixture from a STORED scene.

    python scripts/satlive_build_fixture.py SATWATCH_DIR SCENE_ID [--reply PHRASE TILE FILE ...]

Copies the scene record (without local paths) to
``server/satwatch/fixtures/aerial/<id>/scene.json``, writes a reduced display
``preview.jpg`` (<= 640 px wide, JPEG: display only, detections are computed
on the native pixels) and, from the store's ``<id>.replies/`` (written by a
live run) or from ``--reply`` files, the real SAM replies under ``replies/``.
No network and no Meta call. The native crop is not included; its sha256 is
in scene.json so a re-fetched copy can be compared.
"""
from __future__ import annotations

import argparse
import io
import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from server.satwatch import aerial as ae          # noqa: E402
from server.satwatch import aerial_run as run     # noqa: E402

PREVIEW_PX = 640


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("satwatch_dir")
    ap.add_argument("scene_id")
    ap.add_argument("--reply", nargs=3, action="append", default=[], metavar=("PHRASE", "TILE", "FILE"))
    ap.add_argument("--recorded-at", default=None)
    args = ap.parse_args(argv)
    from PIL import Image
    sd = Path(args.satwatch_dir)
    scene = run.load_scene(sd, args.scene_id, fixtures=Path("/nonexistent"))
    if scene is None or not scene["_stored"]:
        print("no stored scene", file=sys.stderr)
        return 2
    out = ae.FIXTURES / args.scene_id
    (out / "replies").mkdir(parents=True, exist_ok=True)
    meta = {k: v for k, v in scene.items() if not k.startswith("_")}
    recorded = []
    src_dir = run.store_dir(sd) / f"{args.scene_id}.replies"
    for r in scene.get("recorded", []):
        dst = out / "replies" / r["reply"]
        if (src_dir / r["reply"]).is_file():
            shutil.copyfile(src_dir / r["reply"], dst)
        elif not dst.is_file():                    # already in the shipped fixture
            raise SystemExit(f"reply file missing for {r['phrase']} tile {r['tile']}")
        recorded.append(r)
    for phrase, tile, f in args.reply:
        name = run.reply_name(phrase, int(tile))
        shutil.copyfile(f, out / "replies" / name)
        recorded = [r for r in recorded if (r["phrase"], r["tile"]) != (phrase, int(tile))]
        recorded.append({"phrase": phrase, "tile": int(tile), "reply": name, "recorded_at": args.recorded_at})
    meta["recorded"] = sorted(recorded, key=lambda r: (r["phrase"], r["tile"]))
    meta["crop"] = dict(meta["crop"], committed=False)
    (out / "scene.json").write_text(json.dumps(meta, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    raw = (run.store_dir(sd) / scene["crop"]["file"]).read_bytes()
    im = Image.open(io.BytesIO(raw)).convert("RGB")
    s = min(1.0, PREVIEW_PX / im.size[0])
    pv = im.resize((max(1, round(im.size[0] * s)), max(1, round(im.size[1] * s))), Image.LANCZOS)
    buf = io.BytesIO()
    pv.save(buf, "JPEG", quality=68, optimize=True)
    (out / "preview.jpg").write_bytes(buf.getvalue())
    print(f"{args.scene_id}: {len(meta['recorded'])} replies, preview {pv.size} {len(buf.getvalue())} B")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Fetch UN Comtrade extracts locally, with the user's own API key.

UN Comtrade's terms (https://comtrade.un.org/licenseagreement.html) do not allow
copying or redistributing the original records without written permission from
the UN. The repository therefore ships only DERIVED aggregates
(``data/impact/comtrade_aggregates.json``). This script rebuilds the
record-level file the impact layer can also read, for local use only:

    export UN_COMTRADE_API_KEY=<your own subscription key>   # never commit it
    python3 scripts/fetch_comtrade.py [--limit N] [--aggregate]

Output: ``data/impact/comtrade_extracts.json`` (git-ignored, excluded from the
public export and the Docker image). ``--aggregate`` also rewrites the derived
file the repository ships (``data/impact/comtrade_aggregates.json``); review the
terms before publishing any output of this script. Use your own key and respect
the API limits; the script pauses between calls and stops on a 401 or 403.

What it records: the graph countries' reporters (from
``data/graph/build/comtrade_overlay.py`` NODE_M49) x {TOTAL + the HS codes mapped
to the six lenses}, imports, period 2024 first and 2023 where 2024 returns
nothing. The period actually returned is recorded per extract. Reporters or
goods that return nothing are listed as empty or failed, never backfilled.
Net weight is stored only where the API reports a positive value.
Resumable: the output is rewritten atomically after every call.
"""
from __future__ import annotations

import importlib.util
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parent
IMPACT = REPO_ROOT / "data" / "impact"
OUT = IMPACT / "comtrade_extracts.json"
AGG_OUT = IMPACT / "comtrade_aggregates.json"
REPORTERS_REF_CACHE = IMPACT / "reporters_ref.json"
API = "https://comtradeapi.un.org/data/v1/get/C/A/HS"
KEY_ENV = "UN_COMTRADE_API_KEY"
REPORTERS_REF_URL = "https://comtradeapi.un.org/files/v1/app/reference/Reporters.json"
PERIODS = ("2024", "2023")
# TOTAL + HS codes mapped to the six lenses:
#   27 fuels (crude/LNG chapter), 2709 crude, 2711 gas (incl. LNG 271111),
#   85 electrical machinery, 8542 electronic integrated circuits,
#   87 vehicles (automotive), 10 cereals (dry bulk & grain).
CMD_CODES = ("TOTAL", "27", "2709", "2711", "85", "8542", "87", "10")
PREVIEW_CODE_OVERRIDES = {840: 842}  # preview dataset: USA is 842 (see docstring)
SLEEP_S = 1.1


def _load_overlay_m49() -> dict:
    path = REPO_ROOT / "data" / "graph" / "build" / "comtrade_overlay.py"
    spec = importlib.util.spec_from_file_location("comtrade_overlay", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # module main() is __main__-guarded; safe
    return dict(mod.NODE_M49)


def http_get_json(url: str, timeout: int = 90, key: str | None = None) -> dict:
    """GET JSON. The key travels in a header only (never in a URL or a message)."""
    headers = {"User-Agent": "supermap-impact-extracts/2.0"}
    if key:
        headers["Ocp-Apim-Subscription-Key"] = key
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def fetch_reporters_ref() -> dict:
    """{m49: {name, iso3}} from the UN Comtrade reporter reference file,
    cached next to the extracts with its retrieval time."""
    if REPORTERS_REF_CACHE.exists():
        try:
            cached = json.loads(REPORTERS_REF_CACHE.read_text(encoding="utf-8"))
            if cached.get("reporters"):
                return cached
        except (OSError, json.JSONDecodeError):
            pass
    payload = http_get_json(REPORTERS_REF_URL, timeout=60)
    reporters = {}
    for row in payload.get("results") or []:
        code = row.get("reporterCode")
        if code is None:
            continue
        reporters[str(code)] = {
            "name": row.get("reporterDesc"),
            "iso3": row.get("reporterCodeIsoAlpha3"),
        }
    out = {"source": "UN Comtrade reporter reference (files/v1/app/reference/Reporters.json)",
           "retrieved_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
           "reporters": reporters}
    REPORTERS_REF_CACHE.write_text(
        json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    return out


def fetch_extract(reporter: int, cmd: str, key: str, getter=None) -> dict:
    """One (reporter, cmd) extract: try PERIODS in order; return the
    record block (possibly with 'empty': true after all periods)."""
    for period in PERIODS:
        params = {"reporterCode": reporter, "period": period, "cmdCode": cmd,
                  "flowCode": "M", "maxRecords": 500, "motCode": "0", "customsCode": "C00",
                  "partner2Code": "0"}
        url = API + "?" + urllib.parse.urlencode(params)
        payload = (getter or http_get_json)(url, key=key)
        recs = payload.get("data") or []
        if not recs:
            continue
        partners = []
        world = None
        for rec in recs:
            entry = {
                "partner_code": rec.get("partnerCode"),
                "partner_name": rec.get("partnerDesc"),
                "partner_iso3": rec.get("partnerISO"),
                "value_usd": rec.get("primaryValue"),
                "net_weight_kg": (rec.get("netWgt")
                                  if isinstance(rec.get("netWgt"), (int, float))
                                  and rec.get("netWgt") > 0 else None),
            }
            if rec.get("partnerCode") == 0:
                world = entry
            else:
                partners.append(entry)
        partners.sort(key=lambda e: e["partner_code"] if e["partner_code"] is not None else -1)
        return {"reporter_code": reporter, "cmd_code": cmd, "period": period,
                "flow": "M", "records_returned": len(recs),
                "world": world, "partners": partners}
    return {"reporter_code": reporter, "cmd_code": cmd, "period": None,
            "flow": "M", "records_returned": 0, "empty": True,
            "world": None, "partners": []}


def main(argv: list) -> int:
    key = os.environ.get(KEY_ENV, "")
    if not key:
        print(f"Set {KEY_ENV} to your own UN Comtrade subscription key "
              "(https://comtradeplus.un.org). Nothing was fetched.", file=sys.stderr)
        return 2
    limit = None
    if "--limit" in argv:
        limit = int(argv[argv.index("--limit") + 1])
    node_m49 = _load_overlay_m49()
    overlay_codes = sorted(set(node_m49.values()))
    reporters = sorted({PREVIEW_CODE_OVERRIDES.get(c, c) for c in overlay_codes})
    ref = fetch_reporters_ref()
    ref_by_code = ref["reporters"]

    if OUT.exists():
        doc = json.loads(OUT.read_text(encoding="utf-8"))
    else:
        doc = {
            "kind": "UN Comtrade record-level extracts (LOCAL ONLY: git-ignored, never published; the repository ships derived aggregates)",
            "source": "UN Comtrade (comtradeapi.un.org/data/v1/get/C/A/HS), user's own key, flow M (imports)",
            "period_policy": "2024 first; 2023 fallback where 2024 returned no records; the period actually returned is recorded per extract",
            "reporter_code_note": "The United States is reporter 842 in the Comtrade data (the overlay M49 840 is mapped to it). All other reporters use their M49 code.",
            "reporters_ref": {"source": ref["source"], "retrieved_at": ref["retrieved_at"]},
            "extracts": [],
            "failures": [],
        }
    done = {(e["reporter_code"], e["cmd_code"]) for e in doc["extracts"]}
    failures = {(f["reporter_code"], f["cmd_code"]) for f in doc["failures"]}

    todo = [(r, c) for r in reporters for c in CMD_CODES if (r, c) not in done]
    if limit is not None:
        todo = todo[:limit]
    print(f"reporters={len(reporters)} cmds={len(CMD_CODES)} "
          f"done={len(done)} todo={len(todo)}", flush=True)
    for i, (rep, cmd) in enumerate(todo, 1):
        last_err = None
        for attempt in range(3):
            try:
                block = fetch_extract(rep, cmd, key)
                block["reporter_name"] = (ref_by_code.get(str(rep)) or {}).get("name")
                block["reporter_iso3"] = (ref_by_code.get(str(rep)) or {}).get("iso3")
                doc["extracts"].append(block)
                done.add((rep, cmd))
                if (rep, cmd) in failures:
                    doc["failures"] = [f for f in doc["failures"]
                                        if (f["reporter_code"], f["cmd_code"]) != (rep, cmd)]
                    failures.discard((rep, cmd))
                print(f"[{i}/{len(todo)}] {rep} {cmd}: "
                      f"{'empty' if block.get('empty') else str(block['records_returned']) + ' records, period ' + str(block['period'])}",
                      flush=True)
                break
            except urllib.error.HTTPError as exc:
                if exc.code in (401, 403):
                    print(f"Comtrade rejected the request (HTTP {exc.code}): check {KEY_ENV}. Stopping.", file=sys.stderr)
                    return 3
                last_err = exc
                wait = (10, 30, 60)[attempt]
                print(f"[{i}/{len(todo)}] {rep} {cmd}: HTTP {exc.code}; sleeping {wait}s", flush=True)
                time.sleep(wait)
                continue
            except (urllib.error.URLError, TimeoutError, json.JSONDecodeError,
                    OSError) as exc:
                last_err = exc
                wait = (10, 30, 60)[attempt]
                print(f"[{i}/{len(todo)}] {rep} {cmd}: attempt {attempt + 1} failed "
                      f"({exc}); sleeping {wait}s", flush=True)
                time.sleep(wait)
        else:
            doc["failures"] = [f for f in doc["failures"]
                                if (f["reporter_code"], f["cmd_code"]) != (rep, cmd)]
            doc["failures"].append({"reporter_code": rep, "cmd_code": cmd,
                                     "error": str(last_err)})
            print(f"[{i}/{len(todo)}] {rep} {cmd}: FAILED ({last_err})", flush=True)
        doc["retrieved_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        doc["extracts"].sort(key=lambda e: (e["reporter_code"], e["cmd_code"]))
        tmp = OUT.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(OUT)
        time.sleep(SLEEP_S)
    if "--aggregate" in argv:
        sys.path.insert(0, str(REPO_ROOT / "server"))
        from impact.comtrade_agg import aggregate_extracts
        routing = json.loads((IMPACT / "routing_sets.json").read_text(encoding="utf-8"))
        agg = aggregate_extracts(doc, routing, time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))
        AGG_OUT.write_text(json.dumps(agg, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"derived aggregates -> {AGG_OUT}", flush=True)
    print(f"done: {len(doc['extracts'])} extracts, {len(doc['failures'])} failures -> {OUT}",
          flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

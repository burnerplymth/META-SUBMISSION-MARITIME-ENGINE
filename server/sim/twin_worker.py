"""Child-process entry for the undisrupted twin.

``python -m <pkg>.twin_worker`` reads one pickled request on stdin,
runs the same ``simulate`` the parent would (empty disruption set,
``light=True``: only the leg-arrival events are produced) and writes
the pickled ``arrival_events`` dict on stdout. Anything else - a crash,
an out-of-memory kill, a bad request - ends with a non-zero exit and an
empty stdout; the parent then computes the twin itself.

Request keys: graph_data, derivation, snapshot, start_epoch, tick_s,
ticks, airspace, storm_cells."""
from __future__ import annotations

import os
import pickle
import sys


def main() -> int:
    cap_mb = os.environ.get("SUPERMAP_TWIN_AS_MB")
    if cap_mb:
        try:
            import resource
            cap = int(cap_mb) * 1024 * 1024
            resource.setrlimit(resource.RLIMIT_AS, (cap, cap))
        except (ImportError, ValueError, OSError):
            pass
    req = pickle.loads(sys.stdin.buffer.read())
    from .simulate import simulate

    twin = simulate(req["graph_data"], req["derivation"], [],
                    req["snapshot"], req["start_epoch"], req["tick_s"],
                    req["ticks"], airspace=req["airspace"],
                    storm_cells=req["storm_cells"], light=True)
    out = pickle.dumps(twin["arrival_events"], protocol=5)
    sys.stdout.buffer.write(out)
    sys.stdout.buffer.flush()
    return 0


if __name__ == "__main__":
    sys.exit(main())

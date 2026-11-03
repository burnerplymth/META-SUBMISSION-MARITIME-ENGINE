"""Graph completeness layer (opt-in sidecar; data/graph/graph.json is never edited).

  build    candidates -> data/graph/extra.json   (python -m graph_extra.build --write)
  loader   load + validate (drops what fails, cascades, refuses on a graph mismatch)
  merge    the sim view and the engine-rev-2 view of graph + sidecar (in memory only)
  runtime  journaled selection -> what a run consumes (refuses a changed file)
"""
from .loader import (Extra, ExtraError, extra_path, file_sha256, load_extra,
                     validate_document)

__all__ = ["Extra", "ExtraError", "extra_path", "file_sha256", "load_extra",
           "validate_document"]

"""Graph services layer (opt-in sidecar; data/graph/graph.json is never edited).

  build   candidates -> data/graph/services.json   (python -m graph_services.build --write)
  loader  load + validate + select (policy, journaled selection)
  legs    selected services -> through legs (sim) and origin-destination routes (rev 2)
  zones   coarse sea zones for placing stated service-level routing (MODELLED)
"""
from .loader import (ERAS, Services, ServicesError, auto_eras, file_sha256,
                     load_services, parse_selection, services_path)

__all__ = ["ERAS", "Services", "ServicesError", "auto_eras", "file_sha256",
           "load_services", "parse_selection", "services_path"]

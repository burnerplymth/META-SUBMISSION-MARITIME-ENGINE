"""Inventory-days reference sidecar DATA ONLY.

  fetch      network, by hand: the open sources -> data/graph_sidecar_sources/ (hashed, stored)
  build      offline and deterministic: sources -> data/graph/inventory_days.json
  inventory_days   loader + validator (drops what fails, refuses a graph mismatch)
  runtime    journaled selection helper for a future engine hook (nothing in server/engine*, sim or portsim calls it)

Opt-in: nothing reads this file unless a caller asks, so every id and byte of every run is unchanged.

The World Bank CPPI port-service sidecar is not included: its source is CC BY-NC 3.0 IGO.
"""
from .common import SidecarError
from .inventory_days import InventoryDays, inventory_days_path, load_inventory_days

__all__ = ["SidecarError", "InventoryDays", "inventory_days_path", "load_inventory_days"]

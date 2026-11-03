"""Deterministic, dependency-free SVG chart engine for reports and exports.

    from reports.charts import render_svg, normalise, chart_csv

See render.py for the contract and spec.py for the ChartSpec schema.
"""
from .render import (caption, chart_csv, chart_table, ensure_norm,  # noqa: F401
                     render_svg, source_line, uid_of)
from .spec import SpecError, TYPES, normalise  # noqa: F401
from .theme import THEMES, page_css  # noqa: F401

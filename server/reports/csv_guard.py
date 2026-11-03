"""The one spreadsheet formula-injection guard for every CSV/XLSX writer.

A string cell whose first non-space character is = + - @, or whose first
character is a TAB or CR (OWASP), is prefixed with a quote so a spreadsheet
opens it as text. Numbers, None and booleans pass through. Used by
reports.exports, reports.charts.render and api.desks_routes."""
from __future__ import annotations

DANGEROUS_LEAD = ("=", "+", "-", "@")
DANGEROUS_FIRST = ("\t", "\r")


def neutralise(value):
    if isinstance(value, str) and (value.lstrip()[:1] in DANGEROUS_LEAD
                                   or value[:1] in DANGEROUS_FIRST):
        return "'" + value
    return value

"""One palette for light, dark and print.

Categorical slots are the validated eight-hue order (blue, orange, aqua,
yellow, magenta, green, violet, red) stepped separately for the dark
surface. Chain channels own fixed slots so a channel keeps its colour in
every chart. All colour is applied through CSS classes and custom
properties: light is the default, dark follows prefers-color-scheme (or an
explicit theme), print forces light."""
from __future__ import annotations

LIGHT = {
    "bg": "#fcfcfb", "ink": "#0b0b0b", "ink2": "#52514e", "mut": "#6b6a65",
    "grid": "#e1e0d9", "axis": "#c3c2b7", "surf": "#fcfcfb",
    "s1": "#2a78d6", "s2": "#eb6834", "s3": "#1baf7a", "s4": "#eda100",
    "s5": "#e87ba4", "s6": "#008300", "s7": "#6250d6", "s8": "#e34948",
    "other": "#898781",
    "c-sourced": "#1b7a46", "c-snapshot": "#2a5d9f",
    "c-modelled": "#4b43a8", "c-simulated": "#9a5b00",
    "c-notserved": "#6b7280", "c-backtest": "#7a2f86", "c-ledger": "#0f6b73",
}
DARK = {
    "bg": "#1a1a19", "ink": "#f0efec", "ink2": "#c3c2b7", "mut": "#a3a29b",
    "grid": "#2c2c2a", "axis": "#383835", "surf": "#1a1a19",
    "s1": "#3987e5", "s2": "#d95926", "s3": "#199e70", "s4": "#c98500",
    "s5": "#d55181", "s6": "#008300", "s7": "#9085e9", "s8": "#e66767",
    "other": "#898781",
    "c-sourced": "#58c58a", "c-snapshot": "#7fb0ee",
    "c-modelled": "#a59cf2", "c-simulated": "#e0a64a",
    "c-notserved": "#9aa3ad", "c-backtest": "#d28be0", "c-ledger": "#5cc6cf",
}
THEMES = ("light", "dark", "auto")

# chain channels -> fixed categorical slot (colour follows the entity)
CHANNEL_SLOT = {"queue": 1, "congestion": 2, "reroute": 3, "mode-shift": 4,
                "inventory": 5, "cost": 6}

CHIP_CLASS = {"SOURCED": "sourced", "SNAPSHOT": "snapshot",
              "MODELLED": "modelled", "SIMULATED": "simulated",
              "NOT SERVED": "notserved", "BACKTEST": "backtest",
              "LEDGER": "ledger"}


def slot_class(slot) -> str:
    """CSS suffix for a categorical slot: '1'..'8', or 'o' (other)."""
    return str(slot) if isinstance(slot, int) and 1 <= slot <= 8 else "o"


def channel_slot(channel):
    return CHANNEL_SLOT.get(str(channel))


def _vars(d: dict) -> str:
    return ";".join(f"--sm-{k}:{v}" for k, v in d.items())


def _classes(prefix: str) -> str:
    L = LIGHT
    p = prefix
    r = [f"{p} .bg{{fill:{L['bg']};fill:var(--sm-bg)}}",
         f"{p} .t{{fill:{L['ink']};fill:var(--sm-ink)}}",
         f"{p} .t2{{fill:{L['ink2']};fill:var(--sm-ink2)}}",
         f"{p} .tm{{fill:{L['mut']};fill:var(--sm-mut)}}",
         f"{p} .gr{{stroke:{L['grid']};stroke:var(--sm-grid);fill:none}}",
         f"{p} .ax{{stroke:{L['axis']};stroke:var(--sm-axis);fill:none}}",
         f"{p} .rg{{stroke:{L['surf']};stroke:var(--sm-surf)}}",
         f"{p} .sf{{fill:{L['surf']};fill:var(--sm-surf)}}",
         f"{p} .cn{{stroke:{L['mut']};stroke:var(--sm-mut);fill:none}}",
         f"{p} .ln{{fill:none;stroke-linejoin:round;stroke-linecap:round}}"]
    for k in list(range(1, 9)) + ["o"]:
        key = f"s{k}" if k != "o" else "other"
        r.append(f"{p} .f{k}{{fill:{L[key]};fill:var(--sm-{key})}}")
        r.append(f"{p} .k{k}{{stroke:{L[key]};stroke:var(--sm-{key})}}")
    for name, cls in CHIP_CLASS.items():
        key = "c-" + cls
        r.append(f"{p} .chip-{cls}{{fill:none;stroke:{L[key]};"
                 f"stroke:var(--sm-{key})}}")
        r.append(f"{p} .chipt-{cls}{{fill:{L[key]};fill:var(--sm-{key})}}")
    r.append(f"{p} text{{font-family:system-ui,-apple-system,'Segoe UI',"
             f"Roboto,sans-serif;font-variant-numeric:tabular-nums}}")
    return "".join(r)


def standalone_css(theme: str) -> str:
    """Stylesheet embedded in a stand-alone SVG file."""
    css = f"svg.sm-chart{{{_vars(LIGHT)}}}"
    if theme == "dark":
        css = f"svg.sm-chart{{{_vars(DARK)}}}"
    elif theme == "auto":
        css += (f"@media (prefers-color-scheme:dark){{"
                f"svg.sm-chart{{{_vars(DARK)}}}}}")
    return css + _classes("svg.sm-chart")


def page_css() -> str:
    """Chart rules for an HTML page that embeds figures inline. The page
    follows the OS scheme except in the print view / on paper."""
    return (f".sm-chart{{{_vars(LIGHT)};max-width:100%;height:auto}}"
            f"@media (prefers-color-scheme:dark){{"
            f":root:not([data-print]) .sm-chart{{{_vars(DARK)}}}}}"
            f"@media print{{.sm-chart{{{_vars(LIGHT)}}}}}"
            + _classes(".sm-chart")
            + "figure.fig{margin:14px 0}figure.fig figcaption{color:"
              "var(--muted);font-size:.85rem;margin-top:4px}"
              "figure.fig svg{display:block}"
              "@media print{figure.fig{break-inside:avoid;"
              "page-break-inside:avoid}}")

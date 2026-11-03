"""Minimal deterministic XLSX writer (zipfile + hand-built OOXML, no
dependency).

    write_workbook(sheets, created="2025-01-01T00:00:00Z", title="...")
        -> bytes of an .xlsx

Sheets come from `table_sheet` (typed columns, frozen header row, filter,
number formats, column widths) or `text_sheet` (cover-style sheets of
styled rows). The same inputs give the same bytes: shared strings are
numbered in first-use order, the zip has fixed 1980-01-01 timestamps and
sorted entries, and the only clock in the file is the `created` argument
the caller pins (the pack passes its pinned as_of).

Cells: None and "" write nothing (an empty cell, never a zero); bool,
int and float are numbers/booleans; everything else is a string (shared,
never a formula, so no formula-injection guard is needed in the cell
itself). Non-finite floats write nothing.
"""
from __future__ import annotations

import io
import math
import re
import zipfile
from dataclasses import dataclass, field
from xml.sax.saxutils import escape

MAX_CELL_CHARS = 32000
NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
NS_R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
_CTRL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f￾￿]")
_BAD_SHEET = re.compile(r"[\[\]:*?/\\]")

# cellXfs index by style key (see STYLES_XML)
XF = {"default": 0, "header": 1, "int": 2, "float": 3, "wrap": 4,
      "title": 5, "section": 6, "muted": 7, "bold": 8}


@dataclass
class Sheet:
    name: str
    rows: list                    # list of list of (value, style_key|None)
    widths: list = field(default_factory=list)
    freeze_header: bool = False
    autofilter: bool = False


def _col_letter(i: int) -> str:
    s = ""
    i += 1
    while i:
        i, r = divmod(i - 1, 26)
        s = chr(65 + r) + s
    return s


def safe_sheet_name(name: str, used: set) -> str:
    n = _BAD_SHEET.sub("_", str(name)).strip("'") or "Sheet"
    n = n[:31]
    base, k = n, 2
    while n.lower() in used:
        suffix = f"_{k}"
        n = base[:31 - len(suffix)] + suffix
        k += 1
    used.add(n.lower())
    return n


def _kind(values) -> str:
    seen_int = seen_float = seen_other = False
    for v in values:
        if v is None or v == "":
            continue
        if isinstance(v, bool):
            seen_other = True
        elif isinstance(v, int):
            seen_int = True
        elif isinstance(v, float):
            if math.isfinite(v):
                seen_float = True
        else:
            seen_other = True
    if seen_other:
        return "text"
    if seen_float:
        return "float"
    return "int" if seen_int else "text"


def _width(header, values) -> float:
    w = len(str(header)) + 2
    for v in list(values)[:200]:
        if v is None or v == "":
            continue
        w = max(w, len(str(v)) + 1)
    return float(max(8, min(60, w)))


def table_sheet(name, headers, rows) -> Sheet:
    """A dataset sheet: bold frozen header, typed columns with number
    formats, widths from content, filter on the header row."""
    cols = list(zip(*rows)) if rows else [[] for _ in headers]
    kinds = [_kind(c) for c in cols] if rows else ["text"] * len(headers)
    body = []
    for r in rows:
        line = []
        for v, k in zip(r, kinds):
            if k == "text" and isinstance(v, (int, float)) \
                    and not isinstance(v, bool):
                v = str(v)
            line.append((v, k if k in ("int", "float") else None))
        body.append(line)
    widths = [_width(h, c) for h, c in zip(headers, cols or
                                           [[] for _ in headers])]
    return Sheet(name, [[(h, "header") for h in headers]] + body, widths,
                 freeze_header=True, autofilter=True)


def text_sheet(name, rows, widths=None) -> Sheet:
    """Cover-style sheet: rows of (value, style) cells, no filter."""
    return Sheet(name, rows, list(widths or []))


class _Strings:
    def __init__(self):
        self.idx, self.items = {}, []

    def add(self, s: str) -> int:
        if s not in self.idx:
            self.idx[s] = len(self.items)
            self.items.append(s)
        return self.idx[s]


def _clean(s) -> str:
    s = _CTRL.sub("", str(s))
    if len(s) > MAX_CELL_CHARS:
        s = s[:MAX_CELL_CHARS - 6] + " [cut]"
    return s


def _cell(ref, v, style, sst: _Strings) -> str:
    if v is None or v == "":
        return ""
    s = XF.get(style or "default", 0)
    sa = f' s="{s}"' if s else ""
    if isinstance(v, bool):
        return f'<c r="{ref}"{sa} t="b"><v>{1 if v else 0}</v></c>'
    if isinstance(v, (int, float)):
        if isinstance(v, float) and not math.isfinite(v):
            return ""
        return f'<c r="{ref}"{sa}><v>{repr(v) if isinstance(v, float) else v}</v></c>'
    i = sst.add(_clean(v))
    return f'<c r="{ref}"{sa} t="s"><v>{i}</v></c>'


def _sheet_xml(sh: Sheet, sst: _Strings, first: bool) -> str:
    nrows = len(sh.rows)
    ncols = max([len(r) for r in sh.rows] + [len(sh.widths), 1])
    out = ['<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n',
           f'<worksheet xmlns="{NS}" xmlns:r="{NS_R}">',
           f'<dimension ref="A1:{_col_letter(ncols - 1)}{max(1, nrows)}"/>']
    sel = ' tabSelected="1"' if first else ""
    if sh.freeze_header:
        out.append(f'<sheetViews><sheetView{sel} workbookViewId="0">'
                   '<pane ySplit="1" topLeftCell="A2" activePane="bottomLeft"'
                   ' state="frozen"/><selection pane="bottomLeft" '
                   'activeCell="A2" sqref="A2"/></sheetView></sheetViews>')
    else:
        out.append(f'<sheetViews><sheetView{sel} workbookViewId="0"/>'
                   '</sheetViews>')
    out.append('<sheetFormatPr defaultRowHeight="15"/>')
    if sh.widths:
        out.append("<cols>" + "".join(
            f'<col min="{i + 1}" max="{i + 1}" width="{w:g}" '
            f'customWidth="1"/>' for i, w in enumerate(sh.widths)) + "</cols>")
    out.append("<sheetData>")
    for ri, row in enumerate(sh.rows):
        cells = "".join(
            _cell(f"{_col_letter(ci)}{ri + 1}",
                  *(cell if isinstance(cell, tuple) else (cell, None)), sst)
            for ci, cell in enumerate(row))
        out.append(f'<row r="{ri + 1}">{cells}</row>' if cells
                   else f'<row r="{ri + 1}"/>')
    out.append("</sheetData>")
    if sh.autofilter and nrows > 1:
        out.append(f'<autoFilter ref="A1:{_col_letter(ncols - 1)}{nrows}"/>')
    out.append("</worksheet>")
    return "".join(out)


STYLES_XML = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
    f'<styleSheet xmlns="{NS}">'
    '<numFmts count="1"><numFmt numFmtId="164" formatCode="0.0#####"/>'
    '</numFmts>'
    '<fonts count="5">'
    '<font><sz val="11"/><name val="Calibri"/><family val="2"/></font>'
    '<font><b/><sz val="11"/><name val="Calibri"/><family val="2"/></font>'
    '<font><b/><sz val="14"/><name val="Calibri"/><family val="2"/></font>'
    '<font><sz val="11"/><color rgb="FF6B6A65"/><name val="Calibri"/>'
    '<family val="2"/></font>'
    '<font><b/><sz val="11"/><color rgb="FF17202A"/><name val="Calibri"/>'
    '<family val="2"/></font></fonts>'
    '<fills count="4"><fill><patternFill patternType="none"/></fill>'
    '<fill><patternFill patternType="gray125"/></fill>'
    '<fill><patternFill patternType="solid"><fgColor rgb="FFE1E0D9"/>'
    '<bgColor indexed="64"/></patternFill></fill>'
    '<fill><patternFill patternType="solid"><fgColor rgb="FFF0EFEC"/>'
    '<bgColor indexed="64"/></patternFill></fill></fills>'
    '<borders count="2"><border><left/><right/><top/><bottom/><diagonal/>'
    '</border><border><left/><right/><top/><bottom style="thin">'
    '<color rgb="FF898781"/></bottom><diagonal/></border></borders>'
    '<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" '
    'borderId="0"/></cellStyleXfs>'
    '<cellXfs count="9">'
    '<xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/>'
    '<xf numFmtId="0" fontId="1" fillId="2" borderId="1" xfId="0" '
    'applyFont="1" applyFill="1" applyBorder="1" applyAlignment="1">'
    '<alignment vertical="center" wrapText="1"/></xf>'
    '<xf numFmtId="1" fontId="0" fillId="0" borderId="0" xfId="0" '
    'applyNumberFormat="1"/>'
    '<xf numFmtId="164" fontId="0" fillId="0" borderId="0" xfId="0" '
    'applyNumberFormat="1"/>'
    '<xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0" '
    'applyAlignment="1"><alignment vertical="top" wrapText="1"/></xf>'
    '<xf numFmtId="0" fontId="2" fillId="0" borderId="0" xfId="0" '
    'applyFont="1"/>'
    '<xf numFmtId="0" fontId="4" fillId="3" borderId="0" xfId="0" '
    'applyFont="1" applyFill="1"/>'
    '<xf numFmtId="0" fontId="3" fillId="0" borderId="0" xfId="0" '
    'applyFont="1" applyAlignment="1"><alignment vertical="top" '
    'wrapText="1"/></xf>'
    '<xf numFmtId="0" fontId="1" fillId="0" borderId="0" xfId="0" '
    'applyFont="1" applyAlignment="1"><alignment vertical="top"/></xf>'
    '</cellXfs>'
    '<cellStyles count="1"><cellStyle name="Normal" xfId="0" '
    'builtinId="0"/></cellStyles></styleSheet>')


def _zip(files: dict) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED,
                         compresslevel=6) as zf:
        for name in sorted(files):
            info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            zf.writestr(info, files[name], compresslevel=6)
    return buf.getvalue()


def write_workbook(sheets: list[Sheet], created: str,
                   title: str = "SupermAP export") -> bytes:
    if not sheets:
        raise ValueError("a workbook needs at least one sheet")
    used: set = set()
    names = [safe_sheet_name(s.name, used) for s in sheets]
    sst = _Strings()
    files = {}
    for i, sh in enumerate(sheets):
        files[f"xl/worksheets/sheet{i + 1}.xml"] = _sheet_xml(
            sh, sst, i == 0).encode("utf-8")
    n = len(sheets)
    sst_xml = ['<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n',
               f'<sst xmlns="{NS}" count="{len(sst.items)}" '
               f'uniqueCount="{len(sst.items)}">']
    for s in sst.items:
        sp = ' xml:space="preserve"' if (s != s.strip() or "\n" in s
                                          or "  " in s) else ""
        sst_xml.append(f"<si><t{sp}>{escape(s)}</t></si>")
    sst_xml.append("</sst>")
    files["xl/sharedStrings.xml"] = "".join(sst_xml).encode("utf-8")
    files["xl/styles.xml"] = STYLES_XML.encode("utf-8")
    files["xl/workbook.xml"] = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        f'<workbook xmlns="{NS}" xmlns:r="{NS_R}">'
        '<bookViews><workbookView activeTab="0"/></bookViews><sheets>'
        + "".join(f'<sheet name="{escape(nm, {chr(34): "&quot;"})}" '
                  f'sheetId="{i + 1}" r:id="rId{i + 1}"/>'
                  for i, nm in enumerate(names))
        + "</sheets></workbook>").encode("utf-8")
    rel = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
           '<Relationships xmlns="http://schemas.openxmlformats.org/package/'
           '2006/relationships">')
    for i in range(n):
        rel += (f'<Relationship Id="rId{i + 1}" Type="{NS_R}/worksheet" '
                f'Target="worksheets/sheet{i + 1}.xml"/>')
    rel += (f'<Relationship Id="rId{n + 1}" Type="{NS_R}/styles" '
            f'Target="styles.xml"/>'
            f'<Relationship Id="rId{n + 2}" Type="{NS_R}/sharedStrings" '
            f'Target="sharedStrings.xml"/></Relationships>')
    files["xl/_rels/workbook.xml.rels"] = rel.encode("utf-8")
    files["_rels/.rels"] = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/'
        '2006/relationships">'
        f'<Relationship Id="rId1" Type="{NS_R}/officeDocument" '
        'Target="xl/workbook.xml"/>'
        '<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/'
        'package/2006/relationships/metadata/core-properties" '
        'Target="docProps/core.xml"/>'
        f'<Relationship Id="rId3" Type="{NS_R}/extended-properties" '
        'Target="docProps/app.xml"/></Relationships>').encode("utf-8")
    files["docProps/core.xml"] = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        '<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/'
        'package/2006/metadata/core-properties" '
        'xmlns:dc="http://purl.org/dc/elements/1.1/" '
        'xmlns:dcterms="http://purl.org/dc/terms/" '
        'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">'
        f'<dc:title>{escape(_clean(title))}</dc:title>'
        '<dc:creator>SupermAP</dc:creator>'
        f'<dcterms:created xsi:type="dcterms:W3CDTF">{escape(created)}'
        '</dcterms:created>'
        f'<dcterms:modified xsi:type="dcterms:W3CDTF">{escape(created)}'
        '</dcterms:modified></cp:coreProperties>').encode("utf-8")
    files["docProps/app.xml"] = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        '<Properties xmlns="http://schemas.openxmlformats.org/'
        'officeDocument/2006/extended-properties">'
        '<Application>SupermAP</Application></Properties>').encode("utf-8")
    ct = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
          '<Types xmlns="http://schemas.openxmlformats.org/package/2006/'
          'content-types">'
          '<Default Extension="rels" ContentType="application/vnd.'
          'openxmlformats-package.relationships+xml"/>'
          '<Default Extension="xml" ContentType="application/xml"/>'
          '<Override PartName="/xl/workbook.xml" ContentType="application/'
          'vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>')
    for i in range(n):
        ct += (f'<Override PartName="/xl/worksheets/sheet{i + 1}.xml" '
               'ContentType="application/vnd.openxmlformats-officedocument.'
               'spreadsheetml.worksheet+xml"/>')
    ct += ('<Override PartName="/xl/styles.xml" ContentType="application/'
           'vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>'
           '<Override PartName="/xl/sharedStrings.xml" ContentType="'
           'application/vnd.openxmlformats-officedocument.spreadsheetml.'
           'sharedStrings+xml"/>'
           '<Override PartName="/docProps/core.xml" ContentType="'
           'application/vnd.openxmlformats-package.core-properties+xml"/>'
           '<Override PartName="/docProps/app.xml" ContentType="'
           'application/vnd.openxmlformats-officedocument.extended-'
           'properties+xml"/></Types>')
    files["[Content_Types].xml"] = ct.encode("utf-8")
    return _zip(files)

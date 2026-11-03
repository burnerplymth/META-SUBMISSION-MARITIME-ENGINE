"""Port Twin catalogue: which ports get a flat map and the rectangle each
one covers.

Selection: the 25 highest-capacity CORE sea ports in
data/graph/graph.json by capacity (TEU/day), plus the named extra ports.
Every id is a real graph node id.  Each port is ONE contiguous rectangle
[min_lon, min_lat, max_lon, max_lat] drawn around its container / cargo terminals by hand from the
place coordinates and the OSM port polygons (not around the graph's
place point, which for some ports is the city centre, e.g. Shanghai
Huangpu, Houston downtown).  Rectangles are capped at 10 OSM API tiles
(0.05 x 0.05 degree each).  Where a port has several distant sites the
largest container site is mapped and the others are listed in `note`
(nothing is silently dropped).
"""
from __future__ import annotations

import math

TILE_DEG = 0.05          # OSM API tile edge (limit 0.25 sq deg per call)
MAX_TILES_PER_PORT = 10

# place_id -> (display name, bbox, note)
_PORTS = {
    "sea_shanghai": ("Shanghai (Yangshan deep-water port)",
                     [122.00, 30.59, 122.10, 30.66],
                     "Yangshan phases I-IV only; Waigaoqiao and Wusongkou "
                     "are separate sites not mapped here. The graph place "
                     "point is the Huangpu river city centre."),
    "sea_singapore": ("Singapore (Pasir Panjang, Tanjong Pagar, Keppel, Brani)",
                      [103.74, 1.25, 103.89, 1.30],
                      "Tuas and Jurong Port are outside this rectangle."),
    "sea_ningbo": ("Ningbo-Zhoushan (Beilun, Chuanshan)",
                   [121.84, 29.86, 121.99, 29.96],
                   "Meishan and the Zhoushan island terminals are outside "
                   "this rectangle."),
    "sea_shenzhen": ("Shenzhen (Yantian)",
                     [114.20, 22.54, 114.30, 22.60],
                     "Yantian only; Shekou, Chiwan, Mawan and Dachan Bay "
                     "(west Shenzhen) are separate sites not mapped here."),
    "sea_qingdao": ("Qingdao (Qianwan)",
                    [120.16, 35.97, 120.31, 36.07],
                    "Qianwan container port; Dongjiakou is not mapped."),
    "sea_busan": ("Busan (Busan New Port)",
                  [128.76, 35.04, 128.86, 35.10],
                  "New Port (Jinhae bay) only; the North Port and Gamman "
                  "terminals at ~129.05E are not mapped."),
    "sea_tianjin": ("Tianjin (Beijiang and Dongjiang)",
                    [117.72, 38.95, 117.82, 39.02], ""),
    "sea_guangzhou": ("Guangzhou (Nansha)",
                      [113.56, 22.62, 113.66, 22.72], ""),
    "sea_jebelali": ("Jebel Ali",
                     [54.98, 24.97, 55.10, 25.04], ""),
    "sea_hongkong": ("Hong Kong (Kwai Tsing)",
                     [114.08, 22.29, 114.16, 22.37], ""),
    "sea_portklang": ("Port Klang (Westports, Northport)",
                      [101.28, 2.98, 101.43, 3.08], ""),
    "sea_rotterdam": ("Rotterdam (Maasvlakte and Europoort)",
                      [3.92, 51.92, 4.17, 51.98],
                      "Botlek, Waalhaven and the inner-city harbours are "
                      "outside this rectangle."),
    "sea_xiamen": ("Xiamen (Haicang, Dongdu)",
                   [118.02, 24.43, 118.12, 24.50], ""),
    "sea_antwerp": ("Antwerp-Bruges (Antwerp left and right bank)",
                    [4.22, 51.25, 4.37, 51.33],
                    "Zeebrugge is a separate site not mapped here."),
    "sea_tanjungpelepas": ("Tanjung Pelepas",
                           [103.50, 1.31, 103.60, 1.38], ""),
    "sea_kaohsiung": ("Kaohsiung",
                      [120.24, 22.54, 120.34, 22.64], ""),
    "sea_laemchabang": ("Laem Chabang",
                        [100.85, 13.04, 100.95, 13.11], ""),
    "sea_losangeles": ("Los Angeles (San Pedro Bay)",
                       [-118.30, 33.70, -118.22, 33.76], ""),
    "sea_tangermed": ("Tanger Med",
                      [-5.55, 35.85, -5.42, 35.92], ""),
    "sea_taicang": ("Taicang",
                    [121.15, 31.55, 121.30, 31.65], ""),
    "sea_longbeach": ("Long Beach (San Pedro Bay)",
                      [-118.24, 33.74, -118.16, 33.79], ""),
    "sea_newyork": ("New York / New Jersey (Newark, Elizabeth, Bayonne)",
                    [-74.20, 40.64, -74.10, 40.74], ""),
    "sea_hamburg": ("Hamburg (Waltershof, Altenwerder)",
                    [9.88, 53.47, 10.03, 53.55], ""),
    "sea_hochiminh": ("Ho Chi Minh City (Cat Lai)",
                      [106.73, 10.72, 106.83, 10.80], ""),
    "sea_mundra": ("Mundra",
                   [69.65, 22.70, 69.80, 22.80], ""),
    "sea_savannah": ("Savannah (Garden City, Ocean Terminal)",
                     [-81.20, 32.08, -81.05, 32.16], ""),
    "sea_houston": ("Houston (Barbours Cut and Bayport)",
                    [-95.05, 29.60, -94.95, 29.70],
                    "The graph place point is downtown Houston; the "
                    "container terminals are ~40 km downstream."),
    "sea_valencia": ("Valencia",
                     [-0.35, 39.42, -0.25, 39.48], ""),
    "sea_piraeus": ("Piraeus",
                    [23.57, 37.91, 23.67, 37.98], ""),
    "sea_santos": ("Santos",
                   [-46.40, -24.00, -46.25, -23.90], ""),
    "sea_durban": ("Durban",
                   [30.95, -29.93, 31.05, -29.83], ""),
    "sea_portsaid": ("Port Said (East Port Said, West Port Said)",
                     [32.28, 31.20, 32.38, 31.30], ""),
    "sea_felixstowe": ("Felixstowe",
                       [1.22, 51.92, 1.32, 51.98], ""),
    "sea_colombo": ("Colombo",
                    [79.82, 6.91, 79.92, 6.97], ""),
}

PORTS = {pid: {"name": v[0], "bbox": v[1], "note": v[2]}
         for pid, v in sorted(_PORTS.items())}


def tiles_for(bbox: list, step: float = TILE_DEG) -> list[list]:
    """Equal-size sub-rectangles of `bbox`, each at most `step` degrees
    on a side, row-major from south-west, rounded to 5 decimals."""
    x0, y0, x1, y1 = bbox
    nx = max(1, math.ceil((x1 - x0) / step - 1e-9))
    ny = max(1, math.ceil((y1 - y0) / step - 1e-9))
    out = []
    for j in range(ny):
        for i in range(nx):
            out.append([round(x0 + (x1 - x0) * i / nx, 5),
                        round(y0 + (y1 - y0) * j / ny, 5),
                        round(x0 + (x1 - x0) * (i + 1) / nx, 5),
                        round(y0 + (y1 - y0) * (j + 1) / ny, 5)])
    return out

"""Geometrické porovnání GTFS shapes bez závislosti na GIS knihovnách.

Cíl není rozhodnout, zda jsou dvě polyčáry bod po bodu stejné, ale zda
popisují stejnou provozní trasu. Drobné posuny a jiné rozložení shape bodů
se proto tolerují; skutečný odklon trasy nikoli.
"""
import math

EARTH_R = 6_371_000.0


def _xy(point, lat0):
    lon, lat = point
    x = math.radians(lon) * EARTH_R * math.cos(math.radians(lat0))
    y = math.radians(lat) * EARTH_R
    return x, y


def _distance(a, b):
    lat0 = (a[1] + b[1]) / 2
    ax, ay = _xy(a, lat0); bx, by = _xy(b, lat0)
    return math.hypot(ax - bx, ay - by)


def line_length(coords):
    return sum(_distance(a, b) for a, b in zip(coords, coords[1:]))


def _point_segment_distance(p, a, b):
    lat0 = (p[1] + a[1] + b[1]) / 3
    px, py = _xy(p, lat0); ax, ay = _xy(a, lat0); bx, by = _xy(b, lat0)
    dx, dy = bx - ax, by - ay
    if dx == 0 and dy == 0:
        return math.hypot(px - ax, py - ay)
    t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy)))
    return math.hypot(px - (ax + t * dx), py - (ay + t * dy))


def _directed_max_distance(points, line):
    if not points or not line: return float("inf")
    if len(line) == 1: return max(_distance(p, line[0]) for p in points)
    return max(min(_point_segment_distance(p, a, b) for a, b in zip(line, line[1:])) for p in points)


def equivalent(a, b, max_deviation_m=15.0, max_length_ratio=0.015, max_endpoint_m=25.0):
    """Vrátí True, pokud dvě geometrie představují prakticky stejnou trasu.

    Kontrolujeme oba směry vzdálenosti bodů od druhé polyčáry, délku i
    koncové body. Tím ignorujeme kosmetické překreslení shapes.txt, ale
    zachytíme skutečný odklon po jiné ulici/trati.
    """
    if len(a) < 2 or len(b) < 2: return a == b
    if _distance(a[0], b[0]) > max_endpoint_m or _distance(a[-1], b[-1]) > max_endpoint_m: return False
    la, lb = line_length(a), line_length(b)
    if max(la, lb) and abs(la - lb) / max(la, lb) > max_length_ratio: return False
    return (_directed_max_distance(a, b) <= max_deviation_m and
            _directed_max_distance(b, a) <= max_deviation_m)

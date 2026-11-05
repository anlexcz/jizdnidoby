#!/usr/bin/env python3
"""Non-destructive v3 export of actual daily regular tram route variants.

Keeps legacy timetable files unchanged. One variant is an actual GTFS trip
stop sequence; frequencies and segment times come only from trips operating
on the selected service date.
"""
from __future__ import annotations

import json
from collections import defaultdict
from functools import lru_cache
from pathlib import Path

import pandas as pd

import tram_segments as ts

MANIFESTS = ts.DATA / "archive" / "manifests"
SNAPSHOTS = ts.DATA / "history" / "snapshots"
CLASSIFICATIONS = ts.DATA / "history" / "classifications"
OUT = ts.DATA / "web" / "timetable_variants_v3"
INDEX = ts.DATA / "web" / "timetable_variants_v3_index.json"
REGULAR = frozenset({"regular_base", "regular_day_variant"})


def _read_csv(path: Path) -> pd.DataFrame:
    return pd.read_csv(path, dtype=str, keep_default_na=False)


def observed_service_days(manifest_dir: Path = MANIFESTS) -> dict[str, tuple[str, str]]:
    """Latest observation of each service day wins; historical days remain."""
    days = {}
    for path in sorted(manifest_dir.glob("*.json")):
        doc = json.loads(path.read_text(encoding="utf-8"))
        for day, snapshot in doc.get("service_dates", {}).items():
            days[str(day)] = (str(doc["observed_on"]), str(snapshot))
    return days


@lru_cache(maxsize=3)
def load_classifications(observed: str) -> pd.DataFrame:
    path = CLASSIFICATIONS / f"{observed}.csv.gz"
    if not path.exists():
        raise FileNotFoundError(f"Missing classifications: {path}")
    rows = _read_csv(path)
    required = {"service_date", "line", "direction_id", "route_variant_id",
                "classification", "clean_trip_count"}
    if missing := required - set(rows.columns):
        raise ValueError(f"Invalid classifications: missing {sorted(missing)}")
    rows["clean_trip_count"] = pd.to_numeric(
        rows["clean_trip_count"], errors="coerce"
    ).fillna(0).astype(int)
    return rows


@lru_cache(maxsize=2)
def load_snapshot(snapshot: str) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    base = SNAPSHOTS / snapshot
    trips = _read_csv(base / "trips.csv.gz")
    stops = _read_csv(base / "stop_times.csv.gz")
    variants = _read_csv(base / "route_variant_stops.csv.gz")
    return trips, stops, variants


def trip_seconds(series: pd.Series) -> pd.Series:
    """GTFS times may extend past midnight (24:xx, 25:xx, etc)."""
    parts = series.astype(str).str.extract(r"^(\d+):(\d{2}):(\d{2})$")
    nums = parts.apply(pd.to_numeric, errors="coerce")
    return nums[0] * 3600 + nums[1] * 60 + nums[2]


def variant_stops(variant_stops_df: pd.DataFrame, ids: set[str]) -> dict[str, list[list[str]]]:
    rows = variant_stops_df[variant_stops_df["route_variant_id"].isin(ids)].copy()
    rows["_sequence"] = pd.to_numeric(rows["stop_sequence"], errors="coerce")
    rows = rows.sort_values(["route_variant_id", "_sequence"], kind="stable")
    return {
        variant: group[["stop_id", "stop_name"]].values.tolist()
        for variant, group in rows.groupby("route_variant_id", sort=False)
    }


def build_day(
    date: str, trips: pd.DataFrame, stop_times: pd.DataFrame,
    variants: pd.DataFrame, classified: pd.DataFrame,
) -> dict[str, dict[str, dict]]:
    """Return {line: {direction: {'variants': [...]}}} for a service day.

    An exported regular variant must be classified as regular and have clean
    trips. Keep exact stop order, including repeated stops and loops.
    """
    classes = classified[
        classified["classification"].isin(REGULAR)
        & classified["clean_trip_count"].gt(0)
    ].copy()
    if classes.empty:
        return {}
    allowed = classes["route_variant_id"].unique().tolist()
    active = trips[
        trips["route_variant_id"].isin(allowed)
        & trips["clean_regular"].astype(str).str.lower().isin(("true", "1"))
    ].copy()
    if active.empty:
        return {}
    keys = set(map(tuple, classes[
        ["line", "direction_id", "route_variant_id"]
    ].itertuples(index=False, name=None)))
    active = active[
        [tuple(row) in keys for row in active[
            ["line", "direction_id", "route_variant_id"]
        ].itertuples(index=False, name=None)]
    ]
    if active.empty:
        return {}

    sequences = variant_stops(variants, set(active["route_variant_id"]))
    if not sequences:
        return {}

    times = stop_times[stop_times["trip_id"].isin(set(active["trip_id"]))].copy()
    times["_sequence"] = pd.to_numeric(times["stop_sequence"], errors="coerce")
    times = times.sort_values(["trip_id", "_sequence"], kind="stable")
    times["position"] = times.groupby("trip_id").cumcount()
    times["departure_sec"] = trip_seconds(times["departure_time"])
    times["arrival_sec"] = trip_seconds(times["arrival_time"])
    times["next_arrival"] = times.groupby("trip_id")["arrival_sec"].shift(-1)
    times["seconds"] = times["next_arrival"] - times["departure_sec"]
    times["hour"] = (times["departure_sec"] // 3600).astype("Int64")
    times = times.merge(active[["trip_id", "route_variant_id"]], on="trip_id", how="inner")
    times = times[
        (times["seconds"] >= 0) & times["seconds"].notna() & times["hour"].notna()
    ]
    medians = times.groupby(["route_variant_id", "hour", "position"])["seconds"].median().round().astype(int)

    starts = stop_times[stop_times["trip_id"].isin(set(active["trip_id"]))].copy()
    starts["_seq"] = pd.to_numeric(starts["stop_sequence"], errors="coerce")
    starts = starts.sort_values(["trip_id", "_seq"], kind="stable").drop_duplicates("trip_id", keep="first")
    starts["start_hour"] = (trip_seconds(starts["departure_time"]) // 3600).astype("Int64")
    starts = starts.merge(active[["trip_id", "route_variant_id"]], on="trip_id", how="inner")
    counts = starts.dropna(subset=["start_hour"]).groupby(["route_variant_id", "start_hour"]).size()

    result = defaultdict(dict)
    class_map = {
        (str(row.line), str(row.direction_id), str(row.route_variant_id)): str(row.classification)
        for row in classes.itertuples(index=False)
    }
    for (line, direction, variant), group in active.groupby(
        ["line", "direction_id", "route_variant_id"], sort=True
    ):
        stops = sequences.get(str(variant), [])
        if len(stops) < 2:
            continue
        hours = {}
        available = medians.loc[variant] if variant in medians.index.get_level_values(0) else None
        if available is not None:
            for h, hour_rows in available.groupby(level=0):
                values = [None] * (len(stops) - 1)
                for (_, pos), value in hour_rows.items():
                    if 0 <= int(pos) < len(values):
                        values[int(pos)] = int(value)
                if any(v is not None for v in values):
                    hours[str(int(h))] = values
        start_counts = {}
        if variant in counts.index.get_level_values(0):
            start_counts = {str(int(h)): int(c) for h, c in counts.loc[variant].items()}
        item = {
            "id": str(variant),
            "classification": class_map[(str(line), str(direction), str(variant))],
            "trip_count": int(len(group)),
            "start_hours": start_counts,
            "stops": stops,
            "hours": hours,
        }
        result[str(line)].setdefault(str(direction), {"variants": []})["variants"].append(item)
    for directions in result.values():
        for entry in directions.values():
            entry["variants"].sort(key=lambda v: (-v["trip_count"], v["id"]))
    return dict(result)


def reference_routes(gtfs_dir: Path | None = None) -> dict[str, dict]:
    """Optional experimental route_stops.txt reference, not used to alter trips."""
    gtfs_dir = gtfs_dir or ts.GTFS_DIR
    paths = [gtfs_dir / name for name in ("routes.txt", "route_stops.txt", "stops.txt")]
    if not all(path.exists() for path in paths):
        return {}
    routes = _read_csv(paths[0])
    route_stops = _read_csv(paths[1])
    stops = _read_csv(paths[2])
    if not {"route_id", "route_short_name"}.issubset(routes.columns):
        return {}
    names = stops.drop_duplicates("stop_id").set_index("stop_id")["stop_name"].to_dict()
    meta = routes.set_index("route_id")["route_short_name"].to_dict()
    route_stops["_seq"] = pd.to_numeric(route_stops["stop_sequence"], errors="coerce")
    references = defaultdict(dict)
    for (route_id, direction), rows in route_stops.groupby(["route_id", "direction_id"]):
        line = str(meta.get(route_id, ""))
        if not line.isdigit() or not 1 <= int(line) <= 37:
            continue
        rows = rows.sort_values("_seq", kind="stable")
        references[line][str(direction)] = [[sid, names.get(sid, sid)] for sid in rows["stop_id"]]
    return dict(references)


def export(manifest_dir: Path = MANIFESTS, out_dir: Path = OUT, index_path: Path = INDEX) -> dict:
    days = observed_service_days(manifest_dir)
    if not days:
        raise ValueError("No archived GTFS service dates")
    out_dir.mkdir(parents=True, exist_ok=True)
    payloads: dict[str, dict] = {}
    references = reference_routes()
    for date, (observed, snapshot) in sorted(days.items()):
        classes = load_classifications(observed)
        relevant = classes[classes["service_date"] == date]
        trips, stops, variants = load_snapshot(snapshot)
        day = build_day(date, trips, stops, variants, relevant)
        for line, directions in day.items():
            if line not in payloads:
                path = out_dir / f"{line}.json"
                payloads[line] = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {
                    "version": 3, "line": line, "dates": {}
                }
            payloads[line]["version"] = 3
            if line in references:
                payloads[line]["reference_routes"] = references[line]
            payloads[line]["dates"][date] = directions
    all_dates = set()
    listing = {}
    for line, payload in payloads.items():
        payload["dates"] = dict(sorted(payload["dates"].items()))
        (out_dir / f"{line}.json").write_text(
            json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8"
        )
    for path in out_dir.glob("*.json"):
        payload = json.loads(path.read_text(encoding="utf-8"))
        dates = sorted(payload.get("dates", {}))
        all_dates.update(dates)
        listing[str(payload.get("line", path.stem))] = {
            "file": f"timetable_variants_v3/{path.name}", "dates": dates,
            "directions": sorted({d for day in payload["dates"].values() for d in day}),
        }
    index = {
        "version": 3, "dates": sorted(all_dates),
        "lines": dict(sorted(listing.items(), key=lambda x: x[0])),
    }
    index_path.write_text(json.dumps(index, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"Variant export v3: {len(listing)} lines, {len(all_dates)} days; legacy untouched")
    return index


if __name__ == "__main__":
    export()

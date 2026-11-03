#!/usr/bin/env python3
"""Content-addressed GTFS archive for tram timetable history.

Creates one small manifest per observation date and deduplicated service-day
snapshots keyed by normalized content hash. A snapshot contains only tram data
needed to reconstruct trips, stop sequences, route variants and operation legs.
"""
from __future__ import annotations

import hashlib
import json
import os
from datetime import date, datetime, timezone
from pathlib import Path

import pandas as pd

import tram_segments as ts

SCHEMA_VERSION = 1
ARCHIVE_DIR = ts.DATA / "archive"
MANIFEST_DIR = ARCHIVE_DIR / "manifests"
SNAPSHOT_DIR = ts.DATA / "history" / "snapshots"

TRIPS_FILE = "trips.csv.gz"
STOP_TIMES_FILE = "stop_times.csv.gz"
VARIANTS_FILE = "route_variants.csv.gz"
VARIANT_STOPS_FILE = "route_variant_stops.csv.gz"
LEGS_FILE = "legs.csv.gz"

TRIP_COLUMNS = [
    "trip_id", "route_id", "line", "direction_id", "service_id", "block_id",
    "trip_headsign", "shape_id", "is_night", "exceptional",
]
STOP_TIME_COLUMNS = [
    "trip_id", "stop_sequence", "stop_id", "stop_name", "stable_stop_key",
    "arrival_time", "departure_time", "trip_operation_type",
]


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1 << 20):
            digest.update(chunk)
    return digest.hexdigest()


def _stable_stop_key(stops: pd.DataFrame) -> pd.Series:
    node = stops.get("asw_node_id", pd.Series("", index=stops.index)).astype(str).str.strip()
    pole = stops.get("asw_stop_id", pd.Series("", index=stops.index)).astype(str).str.strip()
    good = node.ne("") & pole.ne("")
    out = stops["stop_id"].astype(str).copy()
    out.loc[good] = "asw:" + node.loc[good] + "/" + pole.loc[good]
    return out


def route_variant_id(line: str, direction_id: str, stable_keys: list[str]) -> str:
    payload = "\x1f".join([str(line), str(direction_id), *map(str, stable_keys)])
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:20]


def _operation_pattern(values: list[str]) -> str:
    pattern = []
    for value in values:
        value = str(value or "1")
        if not pattern or pattern[-1] != value:
            pattern.append(value)
    return ">".join(pattern)


def split_operation_legs(group: pd.DataFrame) -> list[dict]:
    """Split one ordered trip into contiguous trip_operation_type runs."""
    if group.empty:
        return []
    ordered = group.sort_values("stop_sequence", key=lambda s: pd.to_numeric(s, errors="coerce"))
    rows = list(ordered.itertuples(index=False))
    legs = []
    start = 0
    leg_no = 1
    for i in range(1, len(rows) + 1):
        current_type = str(getattr(rows[start], "trip_operation_type") or "1")
        boundary = i == len(rows) or str(getattr(rows[i], "trip_operation_type") or "1") != current_type
        if not boundary:
            continue
        part = rows[start:i]
        legs.append({
            "trip_id": str(getattr(part[0], "trip_id")),
            "leg_sequence": leg_no,
            "operation_type": current_type,
            "from_stop_sequence": str(getattr(part[0], "stop_sequence")),
            "to_stop_sequence": str(getattr(part[-1], "stop_sequence")),
            "from_stop_id": str(getattr(part[0], "stop_id")),
            "from_stop_name": str(getattr(part[0], "stop_name")),
            "to_stop_id": str(getattr(part[-1], "stop_id")),
            "to_stop_name": str(getattr(part[-1], "stop_name")),
        })
        leg_no += 1
        start = i
    return legs


def _canonical_csv_bytes(frame: pd.DataFrame, columns: list[str]) -> bytes:
    safe = frame.reindex(columns=columns).fillna("").astype(str)
    return safe.to_csv(index=False, lineterminator="\n").encode("utf-8")


def service_snapshot_id(trips: pd.DataFrame, stop_times: pd.DataFrame) -> str:
    """Hash normalized day content without service_date, enabling dedupe across dates."""
    trip_cols = TRIP_COLUMNS + ["route_variant_id", "operation_pattern", "clean_regular"]
    trip_part = trips.sort_values(["line", "direction_id", "trip_id"], kind="stable")
    stop_part = stop_times.copy()
    stop_part["_seq_sort"] = pd.to_numeric(stop_part["stop_sequence"], errors="coerce")
    stop_part = stop_part.sort_values(["trip_id", "_seq_sort"], kind="stable").drop(columns="_seq_sort")
    digest = hashlib.sha256()
    digest.update(_canonical_csv_bytes(trip_part, trip_cols))
    digest.update(b"\0")
    digest.update(_canonical_csv_bytes(stop_part, STOP_TIME_COLUMNS))
    return digest.hexdigest()[:24]


def _load_tram_data() -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    routes = ts.read("routes.txt")
    route_types = pd.to_numeric(routes["route_type"], errors="coerce")
    tram_routes = routes[(route_types == 0) | route_types.between(900, 906)].copy()
    route_name = "route_short_name" if "route_short_name" in tram_routes.columns else "route_id"
    route_meta_cols = ["route_id", route_name]
    if "is_night" in tram_routes.columns:
        route_meta_cols.append("is_night")
    route_meta = tram_routes[route_meta_cols].copy().rename(columns={route_name: "line"})
    if "is_night" not in route_meta.columns:
        route_meta["is_night"] = ""

    trips = ts.read("trips.txt")
    trips = trips[trips["route_id"].isin(set(route_meta["route_id"]))].copy()
    for col in ("direction_id", "block_id", "trip_headsign", "shape_id", "exceptional"):
        if col not in trips.columns:
            trips[col] = ""
    trips = trips.merge(route_meta, on="route_id", how="inner")
    trips = trips[TRIP_COLUMNS].copy()

    stops_header = pd.read_csv(ts.GTFS_DIR / "stops.txt", nrows=0, encoding="utf-8-sig").columns
    stop_cols = ["stop_id", "stop_name"] + [c for c in ("asw_node_id", "asw_stop_id") if c in stops_header]
    stops = ts.read("stops.txt", usecols=stop_cols).drop_duplicates("stop_id")
    stops["stable_stop_key"] = _stable_stop_key(stops)
    stop_lookup = stops[["stop_id", "stop_name", "stable_stop_key"]]

    st_header = pd.read_csv(ts.GTFS_DIR / "stop_times.txt", nrows=0, encoding="utf-8-sig").columns
    read_cols = ["trip_id", "stop_id", "stop_sequence", "arrival_time", "departure_time"]
    has_operation_type = "trip_operation_type" in st_header
    if has_operation_type:
        read_cols.append("trip_operation_type")

    trip_ids = set(trips["trip_id"])
    parts = []
    for chunk in pd.read_csv(
        ts.GTFS_DIR / "stop_times.txt", dtype=str, usecols=read_cols,
        keep_default_na=False, encoding="utf-8-sig", chunksize=500_000,
    ):
        selected = chunk[chunk["trip_id"].isin(trip_ids)]
        if len(selected):
            parts.append(selected)
    stop_times = pd.concat(parts, ignore_index=True) if parts else pd.DataFrame(columns=read_cols)
    if not has_operation_type:
        stop_times["trip_operation_type"] = "1"
    stop_times["trip_operation_type"] = stop_times["trip_operation_type"].replace("", "1")
    stop_times = stop_times.merge(stop_lookup, on="stop_id", how="left")
    stop_times["stop_name"] = stop_times["stop_name"].fillna("")
    stop_times["stable_stop_key"] = stop_times["stable_stop_key"].fillna(stop_times["stop_id"])
    return trips, stop_times[STOP_TIME_COLUMNS], route_meta


def _decorate_trips(trips: pd.DataFrame, stop_times: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    variant_rows = []
    variant_stop_rows = []
    trip_variant = {}
    patterns = {}
    clean = {}

    meta_by_trip = trips.set_index("trip_id", drop=False)
    for trip_id, group in stop_times.groupby("trip_id", sort=False):
        ordered = group.assign(_seq=pd.to_numeric(group["stop_sequence"], errors="coerce")).sort_values("_seq")
        meta = meta_by_trip.loc[trip_id]
        keys = ordered["stable_stop_key"].astype(str).tolist()
        variant = route_variant_id(meta["line"], meta["direction_id"], keys)
        trip_variant[trip_id] = variant
        op_values = ordered["trip_operation_type"].astype(str).replace("", "1").tolist()
        patterns[trip_id] = _operation_pattern(op_values)
        clean[trip_id] = all(v == "1" for v in op_values)
        variant_rows.append({
            "route_variant_id": variant,
            "line": meta["line"],
            "direction_id": meta["direction_id"],
            "first_stop_id": ordered.iloc[0]["stop_id"],
            "first_stop_name": ordered.iloc[0]["stop_name"],
            "last_stop_id": ordered.iloc[-1]["stop_id"],
            "last_stop_name": ordered.iloc[-1]["stop_name"],
            "stop_count": len(ordered),
        })
        for r in ordered.itertuples(index=False):
            variant_stop_rows.append({
                "route_variant_id": variant,
                "stop_sequence": str(r.stop_sequence),
                "stop_id": str(r.stop_id),
                "stop_name": str(r.stop_name),
                "stable_stop_key": str(r.stable_stop_key),
            })

    out = trips.copy()
    out["route_variant_id"] = out["trip_id"].map(trip_variant).fillna("")
    out["operation_pattern"] = out["trip_id"].map(patterns).fillna("")
    out["clean_regular"] = out["trip_id"].map(clean).fillna(False).astype(bool)

    variants = pd.DataFrame(variant_rows).drop_duplicates("route_variant_id") if variant_rows else pd.DataFrame()
    variant_stops = pd.DataFrame(variant_stop_rows).drop_duplicates(
        ["route_variant_id", "stop_sequence", "stop_id"]
    ) if variant_stop_rows else pd.DataFrame()
    return out, variants, variant_stops


def _build_legs(stop_times: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for _, group in stop_times.groupby("trip_id", sort=False):
        rows.extend(split_operation_legs(group))
    return pd.DataFrame(rows)


def _service_dates_for_trams(trips: pd.DataFrame) -> pd.DataFrame:
    services = ts.service_dates()
    services = services[services["service_id"].isin(set(trips["service_id"]))].copy()
    services["service_date"] = pd.to_datetime(services["date"]).dt.date.astype(str)
    return services[["service_id", "service_date"]].drop_duplicates()


def _write_snapshot(
    snapshot_id: str,
    trips: pd.DataFrame,
    stop_times: pd.DataFrame,
    variants: pd.DataFrame,
    variant_stops: pd.DataFrame,
    legs: pd.DataFrame,
) -> None:
    target = SNAPSHOT_DIR / snapshot_id
    if target.exists() and all((target / f).exists() for f in (
        TRIPS_FILE, STOP_TIMES_FILE, VARIANTS_FILE, VARIANT_STOPS_FILE, LEGS_FILE
    )):
        return
    target.mkdir(parents=True, exist_ok=True)

    trips.to_csv(target / TRIPS_FILE, index=False, encoding="utf-8", compression="gzip")
    stop_times.to_csv(target / STOP_TIMES_FILE, index=False, encoding="utf-8", compression="gzip")
    variants.to_csv(target / VARIANTS_FILE, index=False, encoding="utf-8", compression="gzip")
    variant_stops.to_csv(target / VARIANT_STOPS_FILE, index=False, encoding="utf-8", compression="gzip")
    legs.to_csv(target / LEGS_FILE, index=False, encoding="utf-8", compression="gzip")


def _read_feed_info() -> dict:
    path = ts.GTFS_DIR / "feed_info.txt"
    if not path.exists():
        return {}
    frame = ts.read("feed_info.txt")
    if frame.empty:
        return {}
    return {str(k): str(v) for k, v in frame.iloc[0].to_dict().items()}


def archive(observed_on: str | None = None) -> Path:
    observed_on = observed_on or os.environ.get("GTFS_OBSERVED_ON") or date.today().isoformat()
    source_zip = ts.DATA / "PID_GTFS.zip"
    if not source_zip.exists():
        raise FileNotFoundError(f"Chybí {source_zip}; spusť nejdřív denní GTFS pipeline.")

    feed_hash = _sha256_file(source_zip)
    trips, stop_times, _ = _load_tram_data()
    decorated_trips, variants, variant_stops = _decorate_trips(trips, stop_times)
    legs = _build_legs(stop_times)
    service_dates = _service_dates_for_trams(decorated_trips)

    MANIFEST_DIR.mkdir(parents=True, exist_ok=True)
    SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)

    day_map = {}
    unique_snapshots = set()
    for service_date, active_services in service_dates.groupby("service_date", sort=True):
        service_ids = set(active_services["service_id"])
        day_trips = decorated_trips[decorated_trips["service_id"].isin(service_ids)].copy()
        trip_ids = set(day_trips["trip_id"])
        day_stop_times = stop_times[stop_times["trip_id"].isin(trip_ids)].copy()
        variant_ids = set(day_trips["route_variant_id"]) - {""}
        day_variants = variants[variants["route_variant_id"].isin(variant_ids)].copy()
        day_variant_stops = variant_stops[variant_stops["route_variant_id"].isin(variant_ids)].copy()
        day_legs = legs[legs["trip_id"].isin(trip_ids)].copy()
        snapshot_id = service_snapshot_id(day_trips, day_stop_times)
        _write_snapshot(
            snapshot_id, day_trips, day_stop_times, day_variants, day_variant_stops, day_legs
        )
        day_map[service_date] = snapshot_id
        unique_snapshots.add(snapshot_id)

    feed_info = _read_feed_info()
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "observed_on": observed_on,
        "downloaded_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "source_url": ts.FEED_URL,
        "feed_hash_sha256": feed_hash,
        "feed_info": feed_info,
        "service_date_from": min(day_map) if day_map else None,
        "service_date_to": max(day_map) if day_map else None,
        "service_dates": day_map,
        "unique_snapshot_count": len(unique_snapshots),
    }
    path = MANIFEST_DIR / f"{observed_on}.json"
    path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(
        f"GTFS archiv: {observed_on}, {len(day_map)} služebních dnů, "
        f"{len(unique_snapshots)} unikátních denních snapshotů."
    )
    return path


def main() -> None:
    archive()


if __name__ == "__main__":
    main()

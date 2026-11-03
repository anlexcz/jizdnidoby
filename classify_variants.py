#!/usr/bin/env python3
"""Klasifikace archivovaných tramvajových trasových variant.

Vstupem je manifest observed_on a content-addressed service-day snapshoty z
archive_gtfs.py. Výstupem je jedna řádka pro service_date × linka × směr ×
route_variant_id s klasifikací, jistotou a vysvětlením.
"""
from __future__ import annotations

import json
import math
from pathlib import Path

import pandas as pd

import tram_segments as ts

MANIFEST_DIR = ts.DATA / "archive" / "manifests"
SNAPSHOT_DIR = ts.DATA / "history" / "snapshots"
OUT_DIR = ts.DATA / "history" / "classifications"
DEPOT_PATH = ts.DATA / "reference" / "depot_access_points.json"

EXPLICIT_MAP = {
    "7": "depot_out",
    "8": "depot_in",
    "9": "transition_same_line",
    "10": "transition_other_line",
}


def load_depot_points(path: Path = DEPOT_PATH) -> dict[str, list[dict]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    by_name: dict[str, list[dict]] = {}
    for depot in data.get("depots", []):
        for point in depot.get("points", []):
            row = {**point, "depot_id": depot["id"], "depot_name": depot["name"]}
            by_name.setdefault(point["stop_name"], []).append(row)
    return by_name


def explicit_classification(patterns: list[str]) -> tuple[str | None, str | None]:
    values = []
    for pattern in patterns:
        values.extend([p for p in str(pattern).split(">") if p and p != "1"])
    values = list(dict.fromkeys(values))
    if not values:
        return None, None
    if len(values) == 1:
        op = values[0]
        return EXPLICIT_MAP.get(op, "operational"), f"explicit operation_type={op}"
    if "10" in values:
        return "transition_other_line", "explicit operation_type includes 10"
    if "9" in values:
        return "transition_same_line", "explicit operation_type includes 9"
    return "operational", "explicit mixed operation types: " + ",".join(values)


def _is_night(line: str, is_night_values: list[str]) -> bool:
    if any(str(v).strip().lower() in {"1", "true", "yes"} for v in is_night_values):
        return True
    try:
        return 90 <= int(str(line)) <= 99
    except ValueError:
        return False


def _depot_match(name: str, depot_points: dict[str, list[dict]], start: bool) -> dict | None:
    candidates = depot_points.get(str(name), [])
    allowed = [p for p in candidates if p.get("can_start_run" if start else "can_end_run", False)]
    if not allowed:
        return None
    rank = {"strong": 3, "normal": 2, "weak": 1}
    return max(allowed, key=lambda p: rank.get(p.get("strength", "normal"), 0))


def classify_rows(rows: pd.DataFrame, depot_points: dict[str, list[dict]]) -> pd.DataFrame:
    """Classify pre-aggregated service-day variant rows."""
    df = rows.copy()
    if df.empty:
        return df

    group_key = ["service_date", "line", "direction_id"]
    df["clean_share"] = df["clean_trip_count"] / df.groupby(group_key)["clean_trip_count"].transform("sum").replace(0, 1)

    days_per_ld = df.groupby(["line", "direction_id"])["service_date"].nunique().to_dict()
    presence = (
        df[df["clean_trip_count"] > 0]
        .groupby(["line", "direction_id", "route_variant_id"])["service_date"]
        .nunique()
        .to_dict()
    )

    classifications = []
    for r in df.itertuples(index=False):
        line = str(r.line)
        night = _is_night(line, [r.is_night])
        patterns = str(r.operation_patterns).split("|") if str(r.operation_patterns) else []
        explicit, explicit_reason = explicit_classification(patterns)

        if int(r.clean_trip_count) == 0 and int(r.operational_trip_count) > 0:
            classifications.append((explicit or "operational", "explicit", explicit_reason or "non-regular GTFS operation"))
            continue

        total_days = max(1, days_per_ld.get((line, str(r.direction_id)), 1))
        seen_days = presence.get((line, str(r.direction_id), str(r.route_variant_id)), 0)
        prevalence = seen_days / total_days
        share = float(r.clean_share)
        clean_count = int(r.clean_trip_count)

        # Base must be genuinely recurrent, not merely 1 of 2 observed days.
        if seen_days >= 2 and prevalence >= 0.5 and share >= (0.15 if night else 0.20):
            classifications.append(("regular_base", "strong", f"clean variant on {seen_days}/{total_days} days; share={share:.2f}"))
            continue

        min_count = 2 if night else 3
        min_share = 0.20 if night else 0.15
        if clean_count >= min_count and share >= min_share:
            classifications.append(("regular_day_variant", "strong", f"substantial clean service-day variant; trips={clean_count}; share={share:.2f}"))
            continue

        start_point = _depot_match(r.first_stop_name, depot_points, True)
        end_point = _depot_match(r.last_stop_name, depot_points, False)
        low_volume = clean_count <= (2 if night else max(2, math.ceil(float(r.group_clean_total) * 0.20)))

        if low_volume and start_point and not end_point:
            strength = start_point.get("strength", "normal")
            confidence = "strong" if strength == "strong" else "heuristic"
            classifications.append(("depot_out", confidence, f"clean low-volume variant starts at {start_point['depot_name']} access point {r.first_stop_name}; trips={clean_count}"))
            continue
        if low_volume and end_point and not start_point:
            strength = end_point.get("strength", "normal")
            confidence = "strong" if strength == "strong" else "heuristic"
            classifications.append(("depot_in", confidence, f"clean low-volume variant ends at {end_point['depot_name']} access point {r.last_stop_name}; trips={clean_count}"))
            continue

        suffix = f"; also operational patterns={r.operation_patterns}" if int(r.operational_trip_count) else ""
        classifications.append(("unknown", "unknown", f"insufficient evidence; trips={clean_count}; share={share:.2f}; prevalence={prevalence:.2f}{suffix}"))

    df[["classification", "confidence", "classification_reason"]] = pd.DataFrame(classifications, index=df.index)
    return df


def build_rows(manifest: dict) -> pd.DataFrame:
    rows = []
    for service_date, snapshot_id in manifest.get("service_dates", {}).items():
        base = SNAPSHOT_DIR / snapshot_id
        trips = pd.read_csv(base / "trips.csv.gz", dtype=str, keep_default_na=False)
        variants = pd.read_csv(base / "route_variants.csv.gz", dtype=str, keep_default_na=False)
        variant_meta = variants.set_index("route_variant_id") if not variants.empty else pd.DataFrame()

        for (line, direction, variant_id), group in trips.groupby(["line", "direction_id", "route_variant_id"], sort=False):
            clean_mask = group["clean_regular"].astype(str).str.lower().isin({"true", "1"})
            clean_count = int(clean_mask.sum())
            op_count = int((~clean_mask).sum())
            patterns = "|".join(sorted(set(group.loc[~clean_mask, "operation_pattern"]) - {""}))
            meta = variant_meta.loc[variant_id] if variant_id in variant_meta.index else None
            rows.append({
                "service_date": service_date,
                "snapshot_id": snapshot_id,
                "line": line,
                "direction_id": direction,
                "route_variant_id": variant_id,
                "trip_count": len(group),
                "clean_trip_count": clean_count,
                "operational_trip_count": op_count,
                "operation_patterns": patterns,
                "first_stop_name": "" if meta is None else meta["first_stop_name"],
                "last_stop_name": "" if meta is None else meta["last_stop_name"],
                "stop_count": "" if meta is None else meta["stop_count"],
                "is_night": group["is_night"].iloc[0] if "is_night" in group.columns else "",
            })
    df = pd.DataFrame(rows)
    if not df.empty:
        df["group_clean_total"] = df.groupby(["service_date", "line", "direction_id"])["clean_trip_count"].transform("sum")
    return df


def classify_manifest(observed_on: str | None = None) -> Path:
    manifests = sorted(MANIFEST_DIR.glob("*.json"))
    if not manifests:
        raise FileNotFoundError("Chybí GTFS manifesty; spusť nejdřív archive_gtfs.py")
    path = MANIFEST_DIR / f"{observed_on}.json" if observed_on else manifests[-1]
    manifest = json.loads(path.read_text(encoding="utf-8"))
    rows = build_rows(manifest)
    classified = classify_rows(rows, load_depot_points())
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out = OUT_DIR / f"{manifest['observed_on']}.csv.gz"
    classified.to_csv(out, index=False, encoding="utf-8", compression="gzip")
    counts = classified["classification"].value_counts().to_dict() if not classified.empty else {}
    print(f"Klasifikace {manifest['observed_on']}: {len(classified)} variant; {counts}")
    return out


def main() -> None:
    classify_manifest()


if __name__ == "__main__":
    main()

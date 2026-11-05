#!/usr/bin/env python3
"""Export daily timetable views from real, classified GTFS trip variants.

Never assemble a displayed route by searching the longest path through
aggregate stop-to-stop travel times: that combines depot trips with service.
"""
from __future__ import annotations

import json
from collections import defaultdict
from functools import lru_cache
from pathlib import Path

import pandas as pd

import tram_segments as ts

WEB = ts.DATA / "web"
OUT = WEB / "timetables"
STATE = ts.DATA / "state" / "state.csv.gz"
MANIFESTS = ts.DATA / "archive" / "manifests"
SNAPSHOTS = ts.DATA / "history" / "snapshots"
CLASSIFICATIONS = ts.DATA / "history" / "classifications"
REGULAR_CLASSES = {"regular_base", "regular_day_variant"}


def _dump(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")


def _observations() -> dict[str, tuple[str, str]]:
    """Pick the newest observed GTFS snapshot for each service date."""
    selected = {}
    for path in sorted(MANIFESTS.glob("*.json")):
        manifest = json.loads(path.read_text(encoding="utf-8"))
        observed = str(manifest["observed_on"])
        for day, snapshot in manifest.get("service_dates", {}).items():
            selected[str(day)] = (observed, str(snapshot))
    return selected


@lru_cache(maxsize=4)
def _classified(observed: str) -> pd.DataFrame:
    path = CLASSIFICATIONS / f"{observed}.csv.gz"
    if not path.exists():
        raise FileNotFoundError(f"Chybí klasifikace trasových variant {path}")
    frame = pd.read_csv(path, dtype=str, keep_default_na=False)
    required = {"service_date", "line", "direction_id", "route_variant_id",
                "clean_trip_count", "classification"}
    if missing := required - set(frame.columns):
        raise ValueError(f"Klasifikace neobsahuje sloupce: {sorted(missing)}")
    frame["clean_trip_count"] = pd.to_numeric(
        frame["clean_trip_count"], errors="coerce"
    ).fillna(0).astype(int)
    return frame


@lru_cache(maxsize=4)
def _variant_stops(snapshot: str) -> dict[str, list[list[str]]]:
    path = SNAPSHOTS / snapshot / "route_variant_stops.csv.gz"
    if not path.exists():
        raise FileNotFoundError(f"Chybí skutečné pořadí zastávek {path}")
    frame = pd.read_csv(path, dtype=str, keep_default_na=False)
    needed = {"route_variant_id", "stop_sequence", "stop_id", "stop_name"}
    if missing := needed - set(frame.columns):
        raise ValueError(f"Archiv variant neobsahuje sloupce: {sorted(missing)}")
    frame["_seq"] = pd.to_numeric(frame["stop_sequence"], errors="coerce")
    frame = frame.sort_values(["route_variant_id", "_seq"], kind="stable")
    return {
        str(variant): group[["stop_id", "stop_name"]].values.tolist()
        for variant, group in frame.groupby("route_variant_id", sort=False)
    }


def route_candidates(observations: dict[str, tuple[str, str]]) -> dict:
    """Regular routes only; no depot, transition, or unclassified variants."""
    result = defaultdict(list)
    for day, (observed, snapshot) in sorted(observations.items()):
        classified = _classified(observed)
        rows = classified[classified["service_date"] == day]
        rows = rows[
            rows["classification"].isin(REGULAR_CLASSES)
            & rows["clean_trip_count"].gt(0)
        ]
        # Classifications are per service date and use the same snapshot as the manifest.
        for row in rows.itertuples(index=False):
            result[(day, str(row.line), str(row.direction_id))].append({
                "id": str(row.route_variant_id),
                "count": int(row.clean_trip_count),
                "class": str(row.classification),
                "snapshot": snapshot,
            })
    return result


def choose_main_variants(candidates: dict) -> dict:
    """Prefer the most-used real regular trip variant across archived days."""
    scores = defaultdict(lambda: defaultdict(lambda: [0, 0, 0]))
    for (_, line, direction), variants in candidates.items():
        for route in variants:
            score = scores[(line, direction)][route["id"]]
            score[0] += route["count"]
            score[1] += 1
            score[2] += int(route["class"] == "regular_base")
    return {
        key: max(variants, key=lambda variant: (
            variants[variant][0], variants[variant][1],
            variants[variant][2], variant
        ))
        for key, variants in scores.items()
    }


def choose_route(variants: list[dict], primary: str | None) -> dict | None:
    if not variants:
        return None
    return max(variants, key=lambda route: (
        route["id"] == primary,
        route["class"] == "regular_base",
        route["count"],
        route["id"],
    ))


def snapshot_from_route(group: pd.DataFrame, route: list[list[str]], variant_id: str) -> dict:
    """Project per-hour travel times onto consecutive stops of one real trip."""
    if len(route) < 2:
        return {"stops": [], "hours": {}, "variant_id": variant_id}
    values = group.copy()
    values["hour"] = pd.to_numeric(values["hour"], errors="coerce")
    values["median_s"] = pd.to_numeric(values["median_s"], errors="coerce")
    lookup = {
        (str(row.stop_a_id), str(row.stop_b_id), int(row.hour)): int(round(float(row.median_s)))
        for row in values.dropna(subset=["hour", "median_s"]).itertuples(index=False)
    }
    pairs = [(str(a[0]), str(b[0])) for a, b in zip(route, route[1:])]
    hours = {}
    for hour in sorted(int(h) for h in values["hour"].dropna().unique()):
        row = [lookup.get((a, b, hour)) for a, b in pairs]
        if any(value is not None for value in row):
            hours[str(hour)] = row
    return {"stops": route, "hours": hours, "variant_id": variant_id}


def run() -> None:
    if not STATE.exists():
        raise SystemExit(f"Chybí {STATE}")
    observations = _observations()
    if not observations:
        raise SystemExit("Nejsou k dispozici GTFS manifesty pro výběr skutečných tras.")

    candidates = route_candidates(observations)
    primary = choose_main_variants(candidates)

    state = pd.read_csv(STATE, dtype=str, keep_default_na=False)
    required = {"date", "line", "direction_id", "stop_a_id", "stop_a_name",
                "stop_b_id", "stop_b_name", "hour", "median_s"}
    if missing := required - set(state.columns):
        raise SystemExit(f"State neobsahuje sloupce: {', '.join(sorted(missing))}")

    WEB.mkdir(parents=True, exist_ok=True)
    OUT.mkdir(parents=True, exist_ok=True)

    touched: dict[str, dict] = {}
    selected_count, missing_count = 0, 0
    for (date_s, line, direction), group in state.groupby(
        ["date", "line", "direction_id"], sort=True
    ):
        date_s, line, direction = str(date_s), str(line), str(direction)
        if line not in touched:
            path = OUT / f"{line}.json"
            touched[line] = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {
                "version": 2, "line": line, "dates": {}
            }
            touched[line]["version"] = 2

        variants = candidates.get((date_s, line, direction), [])
        chosen = choose_route(variants, primary.get((line, direction)))
        if chosen is None:
            # Do not silently publish the old synthetic route.
            touched[line].setdefault("dates", {}).setdefault(date_s, {})[direction] = {
                "stops": [], "hours": {}, "variant_id": None
            }
            missing_count += 1
            continue

        actual = _variant_stops(chosen["snapshot"]).get(chosen["id"], [])
        if len(actual) < 2:
            raise ValueError(f"Chybí zastávky varianty {line}/{direction}/{chosen['id']}")
        touched[line].setdefault("dates", {}).setdefault(date_s, {})[direction] = (
            snapshot_from_route(group, actual, chosen["id"])
        )
        selected_count += 1

    for line, payload in touched.items():
        payload["dates"] = dict(sorted(payload.get("dates", {}).items()))
        _dump(OUT / f"{line}.json", payload)

    index = {"version": 2, "dates": [], "lines": {}}
    all_dates = set()
    for path in sorted(OUT.glob("*.json")):
        payload = json.loads(path.read_text(encoding="utf-8"))
        line = str(payload.get("line", path.stem))
        dates = sorted(payload.get("dates", {}))
        directions = sorted({
            direction for day in payload.get("dates", {}).values() for direction in day
        })
        index["lines"][line] = {
            "file": f"timetables/{path.name}", "dates": dates, "directions": directions
        }
        all_dates.update(dates)
    index["dates"] = sorted(all_dates)
    _dump(WEB / "timetable_index.json", index)
    print(f"Historie: {selected_count} skutečných variant, "
          f"{missing_count} chybějících kombinací; {len(index['lines'])} linek.")


if __name__ == "__main__":
    run()

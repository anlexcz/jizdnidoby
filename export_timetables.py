#!/usr/bin/env python3
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

import tram_segments as ts

WEB = ts.DATA / "web"
OUT = WEB / "timetables"
STATE = ts.DATA / "state" / "state.csv.gz"


def _dump(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")


def _longest_route(group: pd.DataFrame) -> tuple[list[str], dict[str, str]]:
    edges = group[["stop_a_id", "stop_a_name", "stop_b_id", "stop_b_name"]].drop_duplicates()
    names: dict[str, str] = {}
    adjacency: dict[str, list[str]] = {}
    incoming: dict[str, int] = {}
    for row in edges.itertuples(index=False):
        a, b = str(row.stop_a_id), str(row.stop_b_id)
        names[a] = str(row.stop_a_name)
        names[b] = str(row.stop_b_name)
        adjacency.setdefault(a, [])
        if b not in adjacency[a]:
            adjacency[a].append(b)
        incoming[b] = incoming.get(b, 0) + 1
        incoming.setdefault(a, incoming.get(a, 0))

    roots = [node for node in names if incoming.get(node, 0) == 0] or list(names)
    best: list[str] = []

    def walk(node: str, path: list[str], seen: set[str]) -> None:
        nonlocal best
        if len(path) > len(best):
            best = path[:]
        for nxt in adjacency.get(node, []):
            if nxt in seen:
                continue
            walk(nxt, path + [nxt], seen | {nxt})

    for root in roots:
        walk(root, [root], {root})
    return best, names


def _snapshot(group: pd.DataFrame) -> dict:
    route, names = _longest_route(group)
    if len(route) < 2:
        return {"stops": [], "hours": {}}

    pairs = list(zip(route, route[1:]))
    values = group.copy()
    values["hour"] = pd.to_numeric(values["hour"], errors="coerce").astype("Int64")
    values["median_s"] = pd.to_numeric(values["median_s"], errors="coerce")
    lookup = {
        (str(row.stop_a_id), str(row.stop_b_id), int(row.hour)): int(round(float(row.median_s)))
        for row in values.dropna(subset=["hour", "median_s"]).itertuples(index=False)
    }
    hours: dict[str, list[int | None]] = {}
    for hour in sorted(int(h) for h in values["hour"].dropna().unique()):
        row = [lookup.get((a, b, hour)) for a, b in pairs]
        if any(value is not None for value in row):
            hours[str(hour)] = row

    return {
        "stops": [[stop_id, names.get(stop_id, stop_id)] for stop_id in route],
        "hours": hours,
    }


def run() -> None:
    if not STATE.exists():
        raise SystemExit(f"Chybí {STATE}")

    WEB.mkdir(parents=True, exist_ok=True)
    OUT.mkdir(parents=True, exist_ok=True)
    state = pd.read_csv(STATE, dtype=str, keep_default_na=False)
    required = {
        "date", "line", "direction_id", "stop_a_id", "stop_a_name",
        "stop_b_id", "stop_b_name", "hour", "median_s",
    }
    missing = required - set(state.columns)
    if missing:
        raise SystemExit(f"State neobsahuje sloupce: {', '.join(sorted(missing))}")

    touched: dict[str, dict] = {}
    for (date_s, line, direction), group in state.groupby(["date", "line", "direction_id"], sort=True):
        line = str(line)
        if line not in touched:
            path = OUT / f"{line}.json"
            if path.exists():
                touched[line] = json.loads(path.read_text(encoding="utf-8"))
            else:
                touched[line] = {"version": 1, "line": line, "dates": {}}
        day = touched[line].setdefault("dates", {}).setdefault(str(date_s), {})
        day[str(direction)] = _snapshot(group)

    for line, payload in touched.items():
        payload["dates"] = dict(sorted(payload.get("dates", {}).items()))
        _dump(OUT / f"{line}.json", payload)

    index = {"version": 1, "dates": [], "lines": {}}
    all_dates: set[str] = set()
    for path in sorted(OUT.glob("*.json")):
        payload = json.loads(path.read_text(encoding="utf-8"))
        line = str(payload.get("line", path.stem))
        dates = sorted(payload.get("dates", {}))
        directions = sorted({
            direction
            for day in payload.get("dates", {}).values()
            for direction in day
        })
        index["lines"][line] = {"file": f"timetables/{path.name}", "dates": dates, "directions": directions}
        all_dates.update(dates)
    index["dates"] = sorted(all_dates)
    _dump(WEB / "timetable_index.json", index)
    print(f"Historie jízdních řádů: {len(index['lines'])} linek / {len(index['dates'])} dnů")


if __name__ == "__main__":
    run()

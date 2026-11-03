#!/usr/bin/env python3
"""Denní porovnání PID GTFS – jediný vstupní bod datové pipeline.

Stav jízdních dob se porovnává po konkrétních spojích. Stabilní identita
zastávkového sloupku používá PID asw_node_id + asw_stop_id. Vedle změn se
ukládá i důkaz detekce (počet spárovaných/potvrzujících spojů).

Historická data jsou append-only: tento skript nemaže changes/evidence,
shape_log ani uložené geometrie. Pracovní snapshot se publikuje až po QA a
následné validaci ve workflow.
"""
from __future__ import annotations

import hashlib
import json
import sys
from datetime import date
from pathlib import Path

import pandas as pd

import export_web
import feed_quality
import shape_geometry as sg
import tram_segments as ts

STATE_DIR = ts.DATA / "state"
STATE = STATE_DIR / "state.csv.gz"
TRIP_STATE = STATE_DIR / "trip_state.csv.gz"
META = STATE_DIR / "meta.json"
CHANGES = ts.DATA / "changes.csv"
EVIDENCE = ts.DATA / "evidence.csv"
SHAPE_LOG = ts.DATA / "shape_log.csv"
SHAPES_DIR = ts.DATA / "shapes"

THRESHOLD_S = 60
MATCH_TOLERANCE_S = 10 * 60
MIN_MATCHED_TRIPS = 3
MIN_CHANGED_SHARE = 0.80
PAIR = ["date", "line", "direction_id", "stop_a_id", "stop_b_id"]
TRIP_PAIR = ["date", "line", "direction_id", "stop_a_key", "stop_b_key"]
CHANGE_COLS = [
    "detected_on", "date", "line", "direction_id", "stop_a_id", "stop_a_name",
    "stop_b_id", "stop_b_name", "hour", "old_s", "new_s", "diff_s", "kind",
]
EVIDENCE_COLS = [
    "detected_on", "date", "line", "direction_id", "stop_a_id", "stop_b_id",
    "hour", "matched_trips", "supporting_trips", "support_share", "diff_min_s", "diff_max_s",
]


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1 << 20):
            digest.update(chunk)
    return digest.hexdigest()


def load_state() -> pd.DataFrame | None:
    if not STATE.exists():
        return None
    df = pd.read_csv(STATE, dtype=str, keep_default_na=False)
    df["hour"] = df["hour"].astype(int)
    df["median_s"] = df["median_s"].astype(float)
    df["n_trips"] = df["n_trips"].astype(int)
    return df


def load_trip_state() -> pd.DataFrame | None:
    if not TRIP_STATE.exists():
        return None
    df = pd.read_csv(TRIP_STATE, dtype=str, keep_default_na=False)
    if not {"stop_a_key", "stop_b_key"}.issubset(df.columns):
        print("Předchozí trip_state je ve formátu v1; bezpečná migrace proběhne bez detekce JD.")
        return None
    for column in ("dep_s", "dur_s"):
        df[column] = pd.to_numeric(df[column], errors="coerce")
    return df.dropna(subset=["dep_s", "dur_s"])


def _stop_identity_map() -> pd.Series:
    header = pd.read_csv(ts.GTFS_DIR / "stops.txt", nrows=0, encoding="utf-8-sig").columns
    cols = ["stop_id"] + [c for c in ("asw_node_id", "asw_stop_id") if c in header]
    stops = ts.read("stops.txt", usecols=cols).drop_duplicates("stop_id")
    if {"asw_node_id", "asw_stop_id"}.issubset(stops.columns):
        node = stops["asw_node_id"].astype(str).str.strip()
        pole = stops["asw_stop_id"].astype(str).str.strip()
        good = node.ne("") & pole.ne("")
        stops["stable_key"] = stops["stop_id"]
        stops.loc[good, "stable_key"] = "asw:" + node[good] + "/" + pole[good]
    else:
        stops["stable_key"] = stops["stop_id"]
    return stops.set_index("stop_id")["stable_key"]


def compute_trip_state(trips: pd.DataFrame) -> pd.DataFrame:
    trip_ids = set(trips["trip_id"])
    cols = ["trip_id", "stop_id", "stop_sequence", "arrival_time", "departure_time"]
    parts = []
    for chunk in pd.read_csv(
        ts.GTFS_DIR / "stop_times.txt", dtype=str, usecols=cols,
        keep_default_na=False, encoding="utf-8-sig", chunksize=500_000,
    ):
        selected = chunk[chunk["trip_id"].isin(trip_ids)]
        if len(selected):
            parts.append(selected)

    base_cols = [
        "date", "line", "direction_id", "stop_a_id", "stop_b_id",
        "stop_a_key", "stop_b_key", "dep_s", "dur_s",
    ]
    if not parts:
        return pd.DataFrame(columns=base_cols)

    st = pd.concat(parts, ignore_index=True)
    st["seq"] = pd.to_numeric(st["stop_sequence"], errors="coerce")
    st["arr"] = ts.to_seconds(st["arrival_time"])
    st["dep"] = ts.to_seconds(st["departure_time"])
    st["arr"] = st["arr"].fillna(st["dep"])
    st["dep"] = st["dep"].fillna(st["arr"])
    st = st.sort_values(["trip_id", "seq"])

    grouped = st.groupby("trip_id", sort=False)
    st["stop_a_id"] = grouped["stop_id"].shift(1)
    st["dep_s"] = grouped["dep"].shift(1)
    seg = st.dropna(subset=["stop_a_id", "dep_s", "arr"]).copy()
    seg["dur_s"] = seg["arr"] - seg["dep_s"]
    seg = seg[seg["dur_s"] >= 0].rename(columns={"stop_id": "stop_b_id"})

    stable = _stop_identity_map()
    seg["stop_a_key"] = seg["stop_a_id"].map(stable).fillna(seg["stop_a_id"])
    seg["stop_b_key"] = seg["stop_b_id"].map(stable).fillna(seg["stop_b_id"])
    seg = seg[["trip_id", "stop_a_id", "stop_b_id", "stop_a_key", "stop_b_key", "dep_s", "dur_s"]]
    seg = seg.merge(trips[["trip_id", "service_id", "line", "direction_id"]], on="trip_id", how="inner")

    service_dates = ts.service_dates()
    service_dates = service_dates[service_dates["service_id"].isin(set(trips["service_id"]))].copy()
    service_dates["date"] = pd.to_datetime(service_dates["date"]).dt.date.astype(str)
    return seg.merge(service_dates, on="service_id", how="inner")[base_cols]


def _match_group(old: pd.DataFrame, new: pd.DataFrame) -> list[tuple[float, float, float, float, float]]:
    old_rows = sorted((float(r.dep_s), float(r.dur_s)) for r in old.itertuples())
    new_rows = sorted((float(r.dep_s), float(r.dur_s)) for r in new.itertuples())
    unused = set(range(len(new_rows)))
    matched = []
    for old_dep, old_dur in old_rows:
        candidates = [i for i in unused if abs(new_rows[i][0] - old_dep) <= MATCH_TOLERANCE_S]
        if not candidates:
            continue
        index = min(candidates, key=lambda i: (abs(new_rows[i][0] - old_dep), new_rows[i][0]))
        unused.remove(index)
        new_dep, new_dur = new_rows[index]
        matched.append((old_dep, new_dep, old_dur, new_dur, new_dur - old_dur))
    return matched


def append_evidence(rows: list[dict]) -> None:
    if not rows:
        return
    fresh = pd.DataFrame(rows, columns=EVIDENCE_COLS)
    if EVIDENCE.exists():
        old = pd.read_csv(EVIDENCE, dtype=str, keep_default_na=False)
        fresh = pd.concat([old, fresh.astype(str)], ignore_index=True)
    dedupe = ["detected_on", "date", "line", "direction_id", "stop_a_id", "stop_b_id", "hour"]
    fresh.drop_duplicates(dedupe, keep="last").to_csv(EVIDENCE, index=False, encoding="utf-8")


def compare_trip_times(prev_trips, new_trips, new_hourly, today):
    common_dates = set(prev_trips["date"]) & set(new_trips["date"])
    if not common_dates:
        return pd.DataFrame(columns=CHANGE_COLS)

    previous = prev_trips[prev_trips["date"].isin(common_dates)]
    current = new_trips[new_trips["date"].isin(common_dates)]
    previous_groups = {key: group for key, group in previous.groupby(TRIP_PAIR, sort=False)}
    current_groups = {key: group for key, group in current.groupby(TRIP_PAIR, sort=False)}
    names = new_hourly.drop_duplicates(PAIR).set_index(PAIR)[["stop_a_name", "stop_b_name"]]
    rows, evidence = [], []

    for key in previous_groups.keys() & current_groups.keys():
        matched = _match_group(previous_groups[key], current_groups[key])
        if len(matched) < MIN_MATCHED_TRIPS:
            continue
        by_hour = {}
        for record in matched:
            by_hour.setdefault(int(record[0] // 3600), []).append(record)

        for hour, records in by_hour.items():
            if len(records) < MIN_MATCHED_TRIPS:
                continue
            diffs = pd.Series([round(r[4]) for r in records], dtype="int64")
            changed = diffs[diffs.abs() >= THRESHOLD_S]
            if changed.empty:
                continue
            dominant = int(changed.value_counts().index[0])
            supporting = [r for r in records if round(r[4]) == dominant]
            share = len(supporting) / len(records)
            if share < MIN_CHANGED_SHARE:
                continue

            date_s, line, direction, _a_key, _b_key = key
            group = current_groups[key]
            stop_a = group["stop_a_id"].mode().iloc[0]
            stop_b = group["stop_b_id"].mode().iloc[0]
            try:
                name_row = names.loc[(date_s, line, direction, stop_a, stop_b)]
                stop_a_name, stop_b_name = name_row["stop_a_name"], name_row["stop_b_name"]
            except KeyError:
                stop_a_name = stop_b_name = ""

            old_s = float(pd.Series([r[2] for r in supporting]).median())
            new_s = float(pd.Series([r[3] for r in supporting]).median())
            rows.append({
                "detected_on": today, "date": date_s, "line": line, "direction_id": direction,
                "stop_a_id": stop_a, "stop_a_name": stop_a_name,
                "stop_b_id": stop_b, "stop_b_name": stop_b_name,
                "hour": hour, "old_s": old_s, "new_s": new_s,
                "diff_s": new_s - old_s, "kind": "time_changed",
            })
            evidence.append({
                "detected_on": today, "date": date_s, "line": line, "direction_id": direction,
                "stop_a_id": stop_a, "stop_b_id": stop_b, "hour": hour,
                "matched_trips": len(records), "supporting_trips": len(supporting),
                "support_share": share, "diff_min_s": int(diffs.min()), "diff_max_s": int(diffs.max()),
            })

    append_evidence(evidence)
    return pd.DataFrame(rows, columns=CHANGE_COLS)


def compare(prev, new, today, prev_trips=None, new_trips=None):
    common = set(prev["date"]) & set(new["date"])
    if not common:
        print("Staré a nové okno se nepřekrývají, není co porovnávat.")
        return pd.DataFrame(columns=CHANGE_COLS)

    previous = prev[prev["date"].isin(common)]
    current = new[new["date"].isin(common)]
    print(f"Porovnávám {len(common)} překrývajících se dnů.")
    if prev_trips is not None and new_trips is not None:
        time_changes = compare_trip_times(prev_trips, new_trips, new, today)
        print(f"Detekce podle spojů: {len(time_changes):,} potvrzených změn.")
    else:
        print("Předchozí kompatibilní stav spojů chybí; změny jízdních dob tento běh nepřidávám.")
        time_changes = pd.DataFrame(columns=CHANGE_COLS)

    name_cols = ["stop_a_name", "stop_b_name"]
    old_pairs = previous[PAIR + name_cols].drop_duplicates(PAIR)
    new_pairs = current[PAIR + name_cols].drop_duplicates(PAIR)
    removed = old_pairs.merge(new_pairs[PAIR], on=PAIR, how="left", indicator=True)
    removed = removed[removed["_merge"] == "left_only"].drop(columns="_merge")
    added = new_pairs.merge(old_pairs[PAIR], on=PAIR, how="left", indicator=True)
    added = added[added["_merge"] == "left_only"].drop(columns="_merge")

    def pair_rows(frame, kind):
        out = frame.copy()
        out.insert(0, "detected_on", today)
        out["hour"] = ""
        out["old_s"] = out["new_s"] = out["diff_s"] = float("nan")
        out["kind"] = kind
        return out[CHANGE_COLS]

    result = pd.concat(
        [time_changes[CHANGE_COLS], pair_rows(added, "segment_added"), pair_rows(removed, "segment_removed")],
        ignore_index=True,
    )
    return result.sort_values(["date", "line", "direction_id", "stop_a_id", "hour"], kind="stable")


def append_changes(changes: pd.DataFrame) -> None:
    if not changes.empty:
        changes.to_csv(CHANGES, mode="a", header=not CHANGES.exists(), index=False, encoding="utf-8")


def _load_shape_geometry(shape_hash):
    path = SHAPES_DIR / f"{shape_hash}.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def compute_shapes(trips, today):
    """Append-only historie skutečných variant trasy; kosmetické redrawy ignoruje."""
    shapes_path = ts.GTFS_DIR / "shapes.txt"
    if not shapes_path.exists() or (trips["shape_id"] == "").all():
        print("Feed neobsahuje shapes.txt, trasování přeskakuji.")
        return

    used = set(trips["shape_id"]) - {""}
    cols = ["shape_id", "shape_pt_lat", "shape_pt_lon", "shape_pt_sequence"]
    parts = []
    for chunk in pd.read_csv(
        shapes_path, dtype=str, usecols=cols, keep_default_na=False,
        encoding="utf-8-sig", chunksize=500_000,
    ):
        selected = chunk[chunk["shape_id"].isin(used)]
        if len(selected):
            parts.append(selected)
    if not parts:
        return

    shapes = pd.concat(parts, ignore_index=True)
    shapes["lat"] = pd.to_numeric(shapes["shape_pt_lat"])
    shapes["lon"] = pd.to_numeric(shapes["shape_pt_lon"])
    shapes["seq"] = pd.to_numeric(shapes["shape_pt_sequence"])
    shapes = shapes.sort_values(["shape_id", "seq"])

    id_to_raw, coords_by_raw = {}, {}
    for shape_id, group in shapes.groupby("shape_id", sort=False):
        coords = [[round(float(lon), 6), round(float(lat), 6)] for lon, lat in zip(group["lon"], group["lat"])]
        shape_hash = hashlib.sha1(json.dumps(coords, separators=(",", ":")).encode()).hexdigest()[:12]
        id_to_raw[shape_id] = shape_hash
        coords_by_raw[shape_hash] = coords

    active = trips[trips["shape_id"].isin(id_to_raw)].copy()
    active["raw_hash"] = active["shape_id"].map(id_to_raw)
    raw_current = active.groupby(["raw_hash", "line", "direction_id"]).size().rename("n_trips").reset_index()
    log = (
        pd.read_csv(SHAPE_LOG, dtype=str, keep_default_na=False)
        if SHAPE_LOG.exists()
        else pd.DataFrame(columns=["shape_hash", "line", "direction_id", "first_seen", "last_seen"])
    )

    canonical_rows, equivalent_redraws = [], 0
    for row in raw_current.itertuples(index=False):
        raw_hash, line, direction = row.raw_hash, str(row.line), str(row.direction_id)
        coords, canonical = coords_by_raw[raw_hash], None
        candidates = log[(log["line"].astype(str) == line) & (log["direction_id"].astype(str) == direction)].sort_values("last_seen", ascending=False)
        for old in candidates.itertuples(index=False):
            old_coords = _load_shape_geometry(old.shape_hash)
            if old_coords is not None and sg.equivalent(coords, old_coords):
                canonical = old.shape_hash
                break
        if canonical is None:
            canonical = raw_hash
        elif canonical != raw_hash:
            equivalent_redraws += 1
        canonical_rows.append({
            "shape_hash": canonical, "line": line, "direction_id": direction,
            "n_trips": row.n_trips, "raw_hash": raw_hash,
        })

    current = pd.DataFrame(canonical_rows)
    keys = ["shape_hash", "line", "direction_id"]
    merged = current[keys].drop_duplicates().merge(log, on=keys, how="left")
    is_new = merged["first_seen"].isna() | (merged["first_seen"] == "")
    new_rows = merged[is_new][keys].assign(first_seen=today, last_seen=today)

    log = log.merge(current[keys].drop_duplicates().assign(_now=True), on=keys, how="left")
    log.loc[log["_now"].notna(), "last_seen"] = today
    log = pd.concat([log.drop(columns="_now"), new_rows], ignore_index=True)
    log.to_csv(SHAPE_LOG, index=False, encoding="utf-8")

    SHAPES_DIR.mkdir(parents=True, exist_ok=True)
    written = 0
    new_hashes = set(new_rows["shape_hash"])
    for row in current.itertuples(index=False):
        if row.shape_hash not in new_hashes:
            continue
        path = SHAPES_DIR / f"{row.shape_hash}.json"
        if not path.exists():
            path.write_text(json.dumps(coords_by_raw[row.raw_hash], separators=(",", ":")), encoding="utf-8")
            written += 1
    print(
        f"Tvary tratí: {len(current)} aktivních kombinací, skutečně nových {len(new_rows)}, "
        f"kosmetických redrawů ignorováno {equivalent_redraws}, nových souborů {written}."
    )


def main() -> None:
    today = date.today().isoformat()
    zip_path = ts.get_feed(sys.argv)
    feed_hash = sha256(zip_path)
    previous_meta = json.loads(META.read_text(encoding="utf-8")) if META.exists() else {}
    same_feed = previous_meta.get("feed_sha256") == feed_hash

    required_web = [
        ts.DATA / "web" / name
        for name in ("segments.json", "current.json", "changes.json", "events.json", "regimes.json", "shapes.json")
    ]
    if same_feed and all(path.exists() for path in required_web):
        print("Feed je identický s minulým během a webový export je kompletní; výpočet není potřeba.")
        return

    if not same_feed:
        new, trips = ts.compute(zip_path)
        new_trips = compute_trip_state(trips)
        previous = load_state()
        previous_trips = load_trip_state()

        report = feed_quality.validate(new, new_trips, previous, previous_trips)
        report.print()
        if not report.ok:
            raise SystemExit("Nový GTFS neprošel QA; předchozí stav zůstává beze změny.")

        changes = (
            pd.DataFrame(columns=CHANGE_COLS)
            if previous is None
            else compare(previous, new, today, previous_trips, new_trips)
        )
        if previous is None:
            print("První běh: ukládám výchozí snímek.")
        else:
            print(f"Nalezených změn: {len(changes):,}")
            if len(changes):
                print(changes["kind"].value_counts().to_string())

        append_changes(changes)
        compute_shapes(trips, today)
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        new.to_csv(STATE, index=False, compression="gzip", encoding="utf-8")
        new_trips.to_csv(TRIP_STATE, index=False, compression="gzip", encoding="utf-8")
        META.write_text(
            json.dumps({
                "schema": 2,
                "feed_sha256": feed_hash,
                "updated": today,
                "detector": "matched-trips-v3-stable-stops-evidence",
                "qa": "passed",
            }, separators=(",", ":")),
            encoding="utf-8",
        )
        print(f"Stav uložen lokálně: {STATE}; stav spojů: {TRIP_STATE}")

    # Pokud je feed stejný, ale webový export byl neúplný, pouze ho opravíme
    # z existujícího snapshotu bez vytváření falešné změny.
    export_web.run(zip_path)


if __name__ == "__main__":
    main()

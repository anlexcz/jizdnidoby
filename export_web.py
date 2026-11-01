#!/usr/bin/env python3
"""Krok 3: vyrobí data pro web (data/web/*.json) z feedu, changes.csv a shape_log.csv."""
import json
from datetime import date

import numpy as np
import pandas as pd

import tram_segments as ts

WEB = ts.DATA / "web"


def dump(obj, name):
    (WEB / name).write_text(json.dumps(obj, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")


def chunks(path, cols, ids, col):
    for c in pd.read_csv(path, dtype=str, usecols=cols, keep_default_na=False,
                         encoding="utf-8-sig", chunksize=500_000):
        yield c[c[col].isin(ids)]


def has_col(path, col):
    return col in pd.read_csv(path, nrows=0, encoding="utf-8-sig").columns


def segments():
    """Geometrie každého úseku (linka, směr, zastávka A, zastávka B)."""
    G = ts.GTFS_DIR
    routes = ts.read("routes.txt")
    routes = routes[routes["route_type"].map(ts.is_tram)]
    trips = ts.read("trips.txt")
    trips = trips[trips["route_id"].isin(routes["route_id"])]
    trips = trips.merge(routes[["route_id", "route_short_name"]], on="route_id")
    for c in ("direction_id", "shape_id"):
        if c not in trips:
            trips[c] = ""
    rep = trips.drop_duplicates(["route_short_name", "direction_id", "shape_id"])  # 1 spoj na variantu trasy

    sd_in_st = has_col(G / "stop_times.txt", "shape_dist_traveled")
    cols = ["trip_id", "stop_id", "stop_sequence"] + (["shape_dist_traveled"] if sd_in_st else [])
    st = pd.concat(chunks(G / "stop_times.txt", cols, set(rep["trip_id"]), "trip_id"), ignore_index=True)
    st["seq"] = pd.to_numeric(st["stop_sequence"])
    st = st.sort_values(["trip_id", "seq"])
    st_groups = dict(tuple(st.groupby("trip_id")))

    shape_groups = {}
    sp = G / "shapes.txt"
    if sp.exists():
        sd_in_sh = has_col(sp, "shape_dist_traveled")
        scols = ["shape_id", "shape_pt_lat", "shape_pt_lon", "shape_pt_sequence"] + (
            ["shape_dist_traveled"] if sd_in_sh else [])
        shp = pd.concat(chunks(sp, scols, set(rep["shape_id"]) - {""}, "shape_id"), ignore_index=True)
        shp["seq"] = pd.to_numeric(shp["shape_pt_sequence"])
        shape_groups = {k: g.sort_values("seq") for k, g in shp.groupby("shape_id")}

    stops = ts.read("stops.txt", usecols=["stop_id", "stop_name", "stop_lat", "stop_lon"]).drop_duplicates("stop_id")
    stops = stops.set_index("stop_id")
    out, straight = {}, 0
    for r in rep.itertuples():
        g = st_groups.get(r.trip_id)
        if g is None:
            continue
        lonlat = sd = None
        sg = shape_groups.get(r.shape_id)
        if sg is not None:
            lonlat = np.column_stack([pd.to_numeric(sg["shape_pt_lon"]), pd.to_numeric(sg["shape_pt_lat"])])
            if "shape_dist_traveled" in sg and "shape_dist_traveled" in g:
                sd = pd.to_numeric(sg["shape_dist_traveled"], errors="coerce").to_numpy()
                if np.isnan(sd).any() or (np.diff(sd) < 0).any():
                    sd = None
        rows = list(g.itertuples())
        for n, (a, b) in enumerate(zip(rows, rows[1:])):
            k = f"{r.route_short_name}|{r.direction_id}|{a.stop_id}|{b.stop_id}"
            if k in out or a.stop_id not in stops.index or b.stop_id not in stops.index:
                continue
            geom = None
            if sd is not None:
                da = pd.to_numeric(a.shape_dist_traveled, errors="coerce")
                db = pd.to_numeric(b.shape_dist_traveled, errors="coerce")
                if pd.notna(da) and pd.notna(db) and db > da:
                    i, j = np.searchsorted(sd, da - 0.5, "left"), np.searchsorted(sd, db + 0.5, "right")
                    if j - i >= 2:
                        geom = lonlat[i:j]
            if geom is None:  # záloha: přímá čára mezi zastávkami
                straight += 1
                geom = np.array([[float(stops.at[s, "stop_lon"]), float(stops.at[s, "stop_lat"])]
                                 for s in (a.stop_id, b.stop_id)])
            out[k] = {"k": k, "l": r.route_short_name, "d": r.direction_id, "i": n,
                      "n": [stops.at[a.stop_id, "stop_name"], stops.at[b.stop_id, "stop_name"]],
                      "g": np.round(geom, 5).tolist()}
    print(f"Geometrie úseků: {len(out)} (z toho {straight} přímou čarou)")
    return [out[k] for k in sorted(out)]


def current():
    """Aktuální jízdní doby: pro každý typ dne (w = Po-Pá, s = So, n = Ne) nejbližší den z okna feedu."""
    p = ts.DATA / "state" / "state.csv.gz"
    if not p.exists():
        return {"dates": {}, "v": {}}
    df = pd.read_csv(p, dtype=str, keep_default_na=False)
    df["hour"] = df["hour"].astype(int)
    df["m"] = df["median_s"].astype(float).round().astype(int)
    df["wd"] = pd.to_datetime(df["date"]).dt.weekday
    today, dates, v = date.today().isoformat(), {}, {}
    for t, f in {"w": lambda x: x < 5, "s": lambda x: x == 5, "n": lambda x: x == 6}.items():
        sub = df[df["wd"].map(f)]
        if sub.empty:
            continue
        future = sub[sub["date"] >= today]
        d = sorted((future if len(future) else sub)["date"].unique())[0]
        dates[t] = d
        for r in sub[sub["date"] == d].itertuples():
            if r.hour < 29:
                arr = v.setdefault(f"{r.line}|{r.direction_id}|{r.stop_a_id}|{r.stop_b_id}", {}).setdefault(t, [0] * 29)
                arr[r.hour] = r.m
    return {"dates": dates, "v": v}


def changes():
    p = ts.DATA / "changes.csv"
    if not p.exists():
        return []
    df = pd.read_csv(p, dtype=str, keep_default_na=False)
    keys = ["detected_on", "line", "direction_id", "stop_a_id", "stop_b_id", "kind"]
    out = []
    for (on, l, d, a, b, kind), g in df.groupby(keys, sort=True):
        rec = {"on": on, "l": l, "d": d, "a": a, "b": b, "t": kind, "f": g["date"].min(),
               "u": g["date"].max(), "n": [g["stop_a_name"].iloc[0], g["stop_b_name"].iloc[0]]}
        if kind == "time_changed":
            g = g.assign(x=pd.to_numeric(g["diff_s"]))
            top = g.loc[g["x"].abs().idxmax()]
            rec.update(x=int(round(top["x"])), o=int(round(float(top["old_s"]))),
                       w=int(round(float(top["new_s"]))), h=sorted({int(h) for h in g["hour"]}))
        out.append(rec)
    return out


def shapes():
    p = ts.DATA / "shape_log.csv"
    if not p.exists():
        return []
    df = pd.read_csv(p, dtype=str, keep_default_na=False)
    return [{"l": r.line, "d": r.direction_id, "h": r.shape_hash, "first": r.first_seen, "last": r.last_seen}
            for r in df.itertuples()]


def run(zip_path):
    ts.unzip(zip_path)
    WEB.mkdir(parents=True, exist_ok=True)
    dump(segments(), "segments.json")
    dump(current(), "current.json")
    ch = changes()
    dump(ch, "changes.json")
    dump(shapes(), "shapes.json")
    print(f"Web data uložena do {WEB} (změn: {len(ch)})")


if __name__ == "__main__":
    import sys
    run(ts.get_feed(sys.argv))

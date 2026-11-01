#!/usr/bin/env python3
"""
Krok 2: denní porovnání.

1. stáhne dnešní feed PID (nebo použije zip zadaný jako argument),
2. spočítá jízdní doby úseků (tram_segments.compute),
3. porovná s včerejším stavem (data/state/state.csv.gz) na dnech, které jsou v obou,
4. přidá změny do data/changes.csv,
5. zaznamená nové tvary tratí do data/shape_log.csv a data/shapes/<hash>.json,
6. uloží nový stav do data/state/.

Použití:
    python daily_update.py               # stáhne feed
    python daily_update.py feed.zip      # použije lokální zip (testování)
"""
import hashlib
import json
import sys
from datetime import date
from pathlib import Path

import pandas as pd

import tram_segments as ts

STATE_DIR = ts.DATA / "state"
STATE = STATE_DIR / "state.csv.gz"
META = STATE_DIR / "meta.json"
CHANGES = ts.DATA / "changes.csv"
SHAPE_LOG = ts.DATA / "shape_log.csv"
SHAPES_DIR = ts.DATA / "shapes"

THRESHOLD_S = 60  # změna jízdní doby od této hodnoty se považuje za změnu
KEY = ["date", "line", "direction_id", "stop_a_id", "stop_b_id", "hour"]
PAIR = ["date", "line", "direction_id", "stop_a_id", "stop_b_id"]
CHANGE_COLS = ["detected_on", "date", "line", "direction_id", "stop_a_id", "stop_a_name",
               "stop_b_id", "stop_b_name", "hour", "old_s", "new_s", "diff_s", "kind"]


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(1 << 20):
            h.update(chunk)
    return h.hexdigest()


def load_state():
    if not STATE.exists():
        return None
    df = pd.read_csv(STATE, dtype=str, keep_default_na=False)
    df["hour"] = df["hour"].astype(int)
    df["median_s"] = df["median_s"].astype(float)
    df["n_trips"] = df["n_trips"].astype(int)
    return df


def compare(prev: pd.DataFrame, new: pd.DataFrame, today: str) -> pd.DataFrame:
    common = set(prev["date"]) & set(new["date"])
    if not common:
        print("Staré a nové okno se nepřekrývají, není co porovnávat.")
        return pd.DataFrame(columns=CHANGE_COLS)
    p = prev[prev["date"].isin(common)]
    n = new[new["date"].isin(common)]
    print(f"Porovnávám {len(common)} překrývajících se dnů.")

    # 1) změna jízdní doby: úsek existoval v obou verzích ve stejné hodině
    m = p.merge(n, on=KEY, how="inner", suffixes=("_old", "_new"))
    m["diff_s"] = m["median_s_new"] - m["median_s_old"]
    ch = m[m["diff_s"].abs() >= THRESHOLD_S].copy()
    time_changes = pd.DataFrame({
        "detected_on": today, "date": ch["date"], "line": ch["line"],
        "direction_id": ch["direction_id"],
        "stop_a_id": ch["stop_a_id"], "stop_a_name": ch["stop_a_name_new"],
        "stop_b_id": ch["stop_b_id"], "stop_b_name": ch["stop_b_name_new"],
        "hour": ch["hour"], "old_s": ch["median_s_old"], "new_s": ch["median_s_new"],
        "diff_s": ch["diff_s"], "kind": "time_changed"})

    # 2) úsek přibyl nebo zmizel (na celý den, bez ohledu na hodinu)
    names = ["stop_a_name", "stop_b_name"]
    po = p[PAIR + names].drop_duplicates(PAIR)
    pn = n[PAIR + names].drop_duplicates(PAIR)
    removed = po.merge(pn[PAIR], on=PAIR, how="left", indicator=True)
    removed = removed[removed["_merge"] == "left_only"].drop(columns="_merge")
    added = pn.merge(po[PAIR], on=PAIR, how="left", indicator=True)
    added = added[added["_merge"] == "left_only"].drop(columns="_merge")

    def pair_rows(df, kind):
        out = df.copy()
        out.insert(0, "detected_on", today)
        out["hour"] = ""
        out["old_s"] = out["new_s"] = out["diff_s"] = float("nan")
        out["kind"] = kind
        return out[CHANGE_COLS]

    res = pd.concat([time_changes[CHANGE_COLS], pair_rows(added, "segment_added"),
                     pair_rows(removed, "segment_removed")], ignore_index=True)
    return res.sort_values(["date", "line", "direction_id", "stop_a_id", "hour"], kind="stable")


def append_changes(changes: pd.DataFrame):
    if changes.empty:
        return
    new_file = not CHANGES.exists()
    changes.to_csv(CHANGES, mode="a", header=new_file, index=False, encoding="utf-8")


def compute_shapes(trips: pd.DataFrame, today: str):
    """Zaznamená tvary tratí. Tvar = otisk souřadnic (5 desetinných míst, cca 1 m)."""
    sp = ts.GTFS_DIR / "shapes.txt"
    if not sp.exists() or (trips["shape_id"] == "").all():
        print("Feed neobsahuje shapes.txt, trasování přeskakuji.")
        return
    used = set(trips["shape_id"]) - {""}
    cols = ["shape_id", "shape_pt_lat", "shape_pt_lon", "shape_pt_sequence"]
    parts = []
    for chunk in pd.read_csv(sp, dtype=str, usecols=cols, keep_default_na=False,
                             encoding="utf-8-sig", chunksize=500_000):
        parts.append(chunk[chunk["shape_id"].isin(used)])
    sh = pd.concat(parts, ignore_index=True)
    sh["lat"] = pd.to_numeric(sh["shape_pt_lat"]).round(5)
    sh["lon"] = pd.to_numeric(sh["shape_pt_lon"]).round(5)
    sh["seq"] = pd.to_numeric(sh["shape_pt_sequence"])
    sh = sh.sort_values(["shape_id", "seq"])

    id_to_hash, coords_by_hash = {}, {}
    for sid, g in sh.groupby("shape_id", sort=False):
        coords = [[lon, lat] for lon, lat in zip(g["lon"], g["lat"])]
        h = hashlib.sha1(json.dumps(coords).encode()).hexdigest()[:12]
        id_to_hash[sid] = h
        coords_by_hash[h] = coords

    t = trips[trips["shape_id"].isin(id_to_hash)].copy()
    t["shape_hash"] = t["shape_id"].map(id_to_hash)
    cur = (t.groupby(["shape_hash", "line", "direction_id"]).size()
           .rename("n_trips").reset_index())

    if SHAPE_LOG.exists():
        log = pd.read_csv(SHAPE_LOG, dtype=str, keep_default_na=False)
    else:
        log = pd.DataFrame(columns=["shape_hash", "line", "direction_id", "first_seen", "last_seen"])
    k = ["shape_hash", "line", "direction_id"]
    merged = cur[k].merge(log, on=k, how="left")
    is_new = merged["first_seen"].isna() | (merged["first_seen"] == "")
    new_rows = merged[is_new][k].assign(first_seen=today, last_seen=today)
    # last_seen aktualizujeme u všech, které jsou v dnešním feedu
    log = log.merge(cur[k].assign(_now=True), on=k, how="left")
    log.loc[log["_now"].notna(), "last_seen"] = today
    log = pd.concat([log.drop(columns="_now"), new_rows], ignore_index=True)
    log.to_csv(SHAPE_LOG, index=False, encoding="utf-8")

    SHAPES_DIR.mkdir(parents=True, exist_ok=True)
    written = 0
    for h in set(new_rows["shape_hash"]):
        f = SHAPES_DIR / f"{h}.json"
        if not f.exists():
            f.write_text(json.dumps(coords_by_hash[h], separators=(",", ":")), encoding="utf-8")
            written += 1
    print(f"Tvary tratí: {len(cur)} aktivních kombinací, nových {len(new_rows)}, "
          f"nových souborů {written}.")


def main():
    today = date.today().isoformat()
    zip_path = ts.get_feed(sys.argv)
    feed_hash = sha256(zip_path)

    prev_meta = json.loads(META.read_text()) if META.exists() else {}
    if prev_meta.get("feed_sha256") == feed_hash:
        print("Feed je identický s minulým během, nic se nemění.")
        return

    new, trips = ts.compute(zip_path)
    prev = load_state()

    if prev is None:
        print("\nPrvní běh: žádný předchozí stav, ukládám výchozí snímek (změny zatím nejsou).")
        changes = pd.DataFrame(columns=CHANGE_COLS)
    else:
        changes = compare(prev, new, today)
        print(f"Nalezených změn: {len(changes):,}")
        if len(changes):
            print(changes["kind"].value_counts().to_string())
            print(changes.head(10).to_string(index=False))
    append_changes(changes)
    compute_shapes(trips, today)

    STATE_DIR.mkdir(parents=True, exist_ok=True)
    new.to_csv(STATE, index=False, compression="gzip", encoding="utf-8")
    META.write_text(json.dumps({"feed_sha256": feed_hash, "updated": today}))
    print(f"\nStav uložen: {STATE} ({STATE.stat().st_size / 1e6:.1f} MB)")


if __name__ == "__main__":
    main()

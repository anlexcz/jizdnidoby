#!/usr/bin/env python3
"""
Krok 1: stáhne GTFS feed PID, vybere tramvaje a spočítá plánované jízdní doby
mezi po sobě jdoucími zastávkami, za každý kalendářní den a každou hodinu.

Použití:
    python tram_segments.py                 # stáhne feed z data.pid.cz
    python tram_segments.py cesta/k/feed.zip  # použije už stažený zip

Výstup: data/out/segments_hourly.csv
Potřebuje: pandas  (pip install pandas)
"""
import sys
import urllib.request
import zipfile
from datetime import timedelta
from pathlib import Path

import pandas as pd

FEED_URL = "https://data.pid.cz/PID_GTFS.zip"
DATA = Path("data")
GTFS_DIR = DATA / "gtfs"
OUT_DIR = DATA / "out"
NEEDED = ["routes.txt", "trips.txt", "stop_times.txt", "stops.txt", "shapes.txt",
          "calendar.txt", "calendar_dates.txt"]  # calendar*.txt a shapes.txt jsou volitelné


def is_tram(route_type: str) -> bool:
    """Základní GTFS: 0 = tramvaj. Rozšířené typy: 900-906 = tramvaj."""
    try:
        t = int(route_type)
    except (TypeError, ValueError):
        return False
    return t == 0 or 900 <= t <= 906


def get_feed(argv) -> Path:
    DATA.mkdir(exist_ok=True)
    if len(argv) > 1:
        return Path(argv[1])
    target = DATA / "PID_GTFS.zip"
    print(f"Stahuji {FEED_URL} ...")
    req = urllib.request.Request(FEED_URL, headers={"User-Agent": "tram-timetable-changes/0.1"})
    with urllib.request.urlopen(req, timeout=120) as r, open(target, "wb") as f:
        while chunk := r.read(1 << 20):
            f.write(chunk)
    print(f"  uloženo: {target} ({target.stat().st_size / 1e6:.1f} MB)")
    return target


def unzip(zip_path: Path):
    GTFS_DIR.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path) as z:
        names = set(z.namelist())
        for n in NEEDED:
            if n in names:
                z.extract(n, GTFS_DIR)
    for must in ["routes.txt", "trips.txt", "stop_times.txt", "stops.txt"]:
        if not (GTFS_DIR / must).exists():
            sys.exit(f"V zipu chybí povinný soubor {must}")
    if not (GTFS_DIR / "calendar.txt").exists() and not (GTFS_DIR / "calendar_dates.txt").exists():
        sys.exit("V zipu chybí calendar.txt i calendar_dates.txt")


def read(name, usecols=None, **kw):
    return pd.read_csv(GTFS_DIR / name, dtype=str, usecols=usecols,
                       keep_default_na=False, encoding="utf-8-sig", **kw)


def service_dates() -> pd.DataFrame:
    """Vrátí tabulku (service_id, date) – dny, kdy daná služba jede."""
    rows = []
    cal_path = GTFS_DIR / "calendar.txt"
    if cal_path.exists():
        cal = read("calendar.txt")
        days = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
        for r in cal.itertuples(index=False):
            start = pd.to_datetime(r.start_date, format="%Y%m%d")
            end = pd.to_datetime(r.end_date, format="%Y%m%d")
            for d in pd.date_range(start, end):
                if getattr(r, days[d.weekday()]) == "1":
                    rows.append((r.service_id, d))
    sd = pd.DataFrame(rows, columns=["service_id", "date"])

    cd_path = GTFS_DIR / "calendar_dates.txt"
    if cd_path.exists():
        cd = read("calendar_dates.txt")
        cd["date"] = pd.to_datetime(cd["date"], format="%Y%m%d")
        add = cd[cd["exception_type"] == "1"][["service_id", "date"]]
        rem = cd[cd["exception_type"] == "2"][["service_id", "date"]]
        sd = pd.concat([sd, add], ignore_index=True)
        if len(rem):
            rem = rem.assign(_rm=True)
            sd = sd.merge(rem, on=["service_id", "date"], how="left")
            sd = sd[sd["_rm"].isna()].drop(columns="_rm")
    return sd.drop_duplicates().reset_index(drop=True)


def to_seconds(s: pd.Series) -> pd.Series:
    """'HH:MM:SS' -> sekundy od půlnoci (HH může být >= 24)."""
    parts = s.str.strip().str.split(":", expand=True)
    return (pd.to_numeric(parts[0], errors="coerce") * 3600
            + pd.to_numeric(parts[1], errors="coerce") * 60
            + pd.to_numeric(parts[2], errors="coerce"))


def compute(zip_path):
    """Vrátí (tabulka úseků po dnech a hodinách, tabulka tramvajových spojů)."""
    unzip(zip_path)

    # --- linky ---
    routes = read("routes.txt")
    print("\nRozložení route_type ve feedu (zkontroluj, že tramvaje jsou správně):")
    print(routes["route_type"].value_counts().to_string())
    routes = routes[routes["route_type"].map(is_tram)]
    name_col = "route_short_name" if "route_short_name" in routes else "route_id"
    print(f"\nTramvajových linek: {len(routes)} -> {sorted(routes[name_col].tolist())}")
    if routes.empty:
        sys.exit("Žádné tramvaje nenalezeny, uprav funkci is_tram().")

    # --- spoje ---
    trips = read("trips.txt")
    trips = trips[trips["route_id"].isin(routes["route_id"])]
    if "direction_id" not in trips:
        trips["direction_id"] = ""
    if "shape_id" not in trips:
        trips["shape_id"] = ""
    trips = trips[["trip_id", "route_id", "service_id", "direction_id", "shape_id"]]
    trips = trips.merge(routes[["route_id", name_col]].rename(columns={name_col: "line"}), on="route_id")
    print(f"Tramvajových spojů (celý feed): {len(trips):,}")

    # --- stop_times po částech (soubor je velký, bereme jen tramvaje) ---
    tram_trip_ids = set(trips["trip_id"])
    cols = ["trip_id", "stop_id", "stop_sequence", "arrival_time", "departure_time"]
    parts = []
    for chunk in pd.read_csv(GTFS_DIR / "stop_times.txt", dtype=str, usecols=cols,
                             keep_default_na=False, encoding="utf-8-sig", chunksize=500_000):
        parts.append(chunk[chunk["trip_id"].isin(tram_trip_ids)])
    st = pd.concat(parts, ignore_index=True)
    print(f"Řádků stop_times pro tramvaje: {len(st):,}")

    st["seq"] = pd.to_numeric(st["stop_sequence"])
    st["arr"] = to_seconds(st["arrival_time"])
    st["dep"] = to_seconds(st["departure_time"])
    # chybějící čas -> doplň druhým (některé feedy mají jen jeden)
    st["arr"] = st["arr"].fillna(st["dep"])
    st["dep"] = st["dep"].fillna(st["arr"])
    st = st.sort_values(["trip_id", "seq"]).reset_index(drop=True)

    # --- úseky: dvojice po sobě jdoucích zastávek na spoji ---
    g = st.groupby("trip_id", sort=False)
    st["stop_a"] = g["stop_id"].shift(1)
    st["dep_a"] = g["dep"].shift(1)
    seg = st.dropna(subset=["stop_a", "dep_a", "arr"]).copy()
    seg["dur"] = seg["arr"] - seg["dep_a"]
    seg = seg[seg["dur"] >= 0]
    seg["hour"] = (seg["dep_a"] // 3600).astype(int)  # hodina odjezdu ze zastávky A; může být >= 24
    seg = seg.rename(columns={"stop_id": "stop_b"})[["trip_id", "stop_a", "stop_b", "hour", "dur"]]
    seg = seg.merge(trips[["trip_id", "service_id", "line", "direction_id"]], on="trip_id")
    print(f"Úseků (spoj × dvojice zastávek): {len(seg):,}")

    # --- po dnech ---
    sd = service_dates()
    sd = sd[sd["service_id"].isin(set(trips["service_id"]))]
    dates = sorted(sd["date"].unique())
    print(f"Dnů v okně feedu: {len(dates)} ({pd.Timestamp(dates[0]).date()} až {pd.Timestamp(dates[-1]).date()})")

    key = ["line", "direction_id", "stop_a", "stop_b", "hour"]
    out_parts = []
    for d in dates:
        active = set(sd.loc[sd["date"] == d, "service_id"])
        day = seg[seg["service_id"].isin(active)]
        if day.empty:
            continue
        agg = (day.groupby(key, sort=False)["dur"]
               .agg(median_s="median", n_trips="count").reset_index())
        agg.insert(0, "date", pd.Timestamp(d).date().isoformat())
        out_parts.append(agg)
    out = pd.concat(out_parts, ignore_index=True)
    out["median_s"] = out["median_s"].round(1)

    # --- názvy zastávek ---
    stops = read("stops.txt", usecols=["stop_id", "stop_name"]).drop_duplicates("stop_id")
    nm = stops.set_index("stop_id")["stop_name"]
    out["stop_a_name"] = out["stop_a"].map(nm)
    out["stop_b_name"] = out["stop_b"].map(nm)
    out = out.rename(columns={"stop_a": "stop_a_id", "stop_b": "stop_b_id"})
    out = out[["date", "line", "direction_id", "stop_a_id", "stop_a_name",
               "stop_b_id", "stop_b_name", "hour", "median_s", "n_trips"]]

    return out, trips


def main():
    zip_path = get_feed(sys.argv)
    out, _ = compute(zip_path)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out_path = OUT_DIR / "segments_hourly.csv"
    out.to_csv(out_path, index=False, encoding="utf-8")
    print(f"\nHotovo: {out_path} ({len(out):,} řádků)")
    print("Unikátních úseků:", out[["line", "direction_id", "stop_a_id", "stop_b_id"]].drop_duplicates().shape[0])
    print("\nUkázka:")
    print(out.head(8).to_string(index=False))


if __name__ == "__main__":
    main()

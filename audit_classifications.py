#!/usr/bin/env python3
from pathlib import Path
import pandas as pd

ROOT = Path(__file__).resolve().parent
CLASS_DIR = ROOT / "data" / "history" / "classifications"


def main():
    files = sorted(CLASS_DIR.glob("*.csv.gz"))
    if not files:
        raise SystemExit("No classification files")
    path = files[-1]
    df = pd.read_csv(path, dtype=str, keep_default_na=False)
    for c in ("trip_count","clean_trip_count","operational_trip_count","group_clean_total","clean_share"):
        if c in df:
            df[c] = pd.to_numeric(df[c], errors="coerce").fillna(0)

    print(f"AUDIT {path.name}: {len(df)} rows")
    print("COUNTS", df["classification"].value_counts().to_dict())

    unk = df[df.classification == "unknown"].copy()
    print(f"UNKNOWN rows={len(unk)}, unique variants={unk.route_variant_id.nunique()}")
    if len(unk):
        u = (unk.groupby(["line","direction_id","route_variant_id","first_stop_name","last_stop_name"], as_index=False)
             .agg(days=("service_date","nunique"), trips=("clean_trip_count","sum"), max_share=("clean_share","max")))
        u = u.sort_values(["days","trips","max_share"], ascending=False)
        print("TOP UNKNOWN UNIQUE")
        print(u.head(80).to_csv(index=False))

    dep = df[df.classification.isin(["depot_out","depot_in"])].copy()
    print(f"DEPOT rows={len(dep)}, unique variants={dep.route_variant_id.nunique()}")
    if len(dep):
        d = (dep.groupby(["classification","line","direction_id","route_variant_id","first_stop_name","last_stop_name","confidence"], as_index=False)
             .agg(days=("service_date","nunique"), trips=("clean_trip_count","sum"), max_trips_day=("clean_trip_count","max"), max_share=("clean_share","max")))
        # Suspicious depot: appears on many days with substantial clean share or >2 trips/day.
        sus = d[(d.days >= 7) | (d.max_trips_day > 2) | (d.max_share >= .20)].sort_values(["max_share","max_trips_day","days"], ascending=False)
        print(f"SUSPICIOUS DEPOT unique={len(sus)}")
        print(sus.head(120).to_csv(index=False))

    # A variant that receives several semantic classes over the observation window deserves inspection.
    mix = (df.groupby(["line","direction_id","route_variant_id","first_stop_name","last_stop_name"])["classification"]
             .agg(lambda s: "|".join(sorted(set(s))))
             .reset_index(name="classes"))
    mix = mix[mix.classes.str.contains("\\|")]
    print(f"MIXED-CLASS unique={len(mix)}")
    print(mix.head(100).to_csv(index=False))

if __name__ == "__main__":
    main()

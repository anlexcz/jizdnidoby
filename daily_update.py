#!/usr/bin/env python3
"""Denní porovnání PID GTFS s detekcí změn podle konkrétních spojů."""
import hashlib
import json
import sys
from datetime import date
from pathlib import Path

import pandas as pd

import export_web
import feed_quality
import tram_segments as ts

STATE_DIR = ts.DATA / "state"
STATE = STATE_DIR / "state.csv.gz"
TRIP_STATE = STATE_DIR / "trip_state.csv.gz"
META = STATE_DIR / "meta.json"
CHANGES = ts.DATA / "changes.csv"
SHAPE_LOG = ts.DATA / "shape_log.csv"
SHAPES_DIR = ts.DATA / "shapes"

THRESHOLD_S = 60
MATCH_TOLERANCE_S = 10 * 60
MIN_MATCHED_TRIPS = 3
MIN_CHANGED_SHARE = 0.80
PAIR = ["date", "line", "direction_id", "stop_a_id", "stop_b_id"]
TRIP_PAIR = PAIR
CHANGE_COLS = ["detected_on", "date", "line", "direction_id", "stop_a_id", "stop_a_name",
               "stop_b_id", "stop_b_name", "hour", "old_s", "new_s", "diff_s", "kind"]


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(1 << 20): h.update(chunk)
    return h.hexdigest()


def load_state():
    if not STATE.exists(): return None
    df = pd.read_csv(STATE, dtype=str, keep_default_na=False)
    df["hour"] = df["hour"].astype(int); df["median_s"] = df["median_s"].astype(float); df["n_trips"] = df["n_trips"].astype(int)
    return df


def load_trip_state():
    if not TRIP_STATE.exists(): return None
    df = pd.read_csv(TRIP_STATE, dtype=str, keep_default_na=False)
    for c in ["dep_s", "dur_s"]: df[c] = pd.to_numeric(df[c], errors="coerce")
    return df.dropna(subset=["dep_s", "dur_s"])


def compute_trip_state(trips):
    trip_ids = set(trips["trip_id"]); cols = ["trip_id","stop_id","stop_sequence","arrival_time","departure_time"]; parts=[]
    for chunk in pd.read_csv(ts.GTFS_DIR/"stop_times.txt", dtype=str, usecols=cols, keep_default_na=False, encoding="utf-8-sig", chunksize=500_000):
        x=chunk[chunk["trip_id"].isin(trip_ids)]
        if len(x): parts.append(x)
    if not parts: return pd.DataFrame(columns=TRIP_PAIR+["dep_s","dur_s"])
    st=pd.concat(parts,ignore_index=True); st["seq"]=pd.to_numeric(st["stop_sequence"],errors="coerce")
    st["arr"]=ts.to_seconds(st["arrival_time"]); st["dep"]=ts.to_seconds(st["departure_time"]); st["arr"]=st["arr"].fillna(st["dep"]); st["dep"]=st["dep"].fillna(st["arr"])
    st=st.sort_values(["trip_id","seq"]); g=st.groupby("trip_id",sort=False); st["stop_a_id"]=g["stop_id"].shift(1); st["dep_s"]=g["dep"].shift(1)
    seg=st.dropna(subset=["stop_a_id","dep_s","arr"]).copy(); seg["dur_s"]=seg["arr"]-seg["dep_s"]; seg=seg[seg["dur_s"]>=0].rename(columns={"stop_id":"stop_b_id"})
    seg=seg[["trip_id","stop_a_id","stop_b_id","dep_s","dur_s"]].merge(trips[["trip_id","service_id","line","direction_id"]],on="trip_id",how="inner")
    sd=ts.service_dates(); sd=sd[sd["service_id"].isin(set(trips["service_id"]))].copy(); sd["date"]=pd.to_datetime(sd["date"]).dt.date.astype(str)
    return seg.merge(sd,on="service_id",how="inner")[["date","line","direction_id","stop_a_id","stop_b_id","dep_s","dur_s"]]


def _match_group(old,new):
    old_rows=sorted([(float(r.dep_s),float(r.dur_s)) for r in old.itertuples()]); new_rows=sorted([(float(r.dep_s),float(r.dur_s)) for r in new.itertuples()]); unused=set(range(len(new_rows))); matched=[]
    for old_dep,old_dur in old_rows:
        cand=[j for j in unused if abs(new_rows[j][0]-old_dep)<=MATCH_TOLERANCE_S]
        if not cand: continue
        j=min(cand,key=lambda x:(abs(new_rows[x][0]-old_dep),new_rows[x][0])); unused.remove(j); new_dep,new_dur=new_rows[j]; matched.append((old_dep,new_dep,old_dur,new_dur,new_dur-old_dur))
    return matched


def compare_trip_times(prev_trips,new_trips,new_hourly,today):
    common=set(prev_trips["date"])&set(new_trips["date"])
    if not common: return pd.DataFrame(columns=CHANGE_COLS)
    p=prev_trips[prev_trips["date"].isin(common)]; n=new_trips[new_trips["date"].isin(common)]
    pg={k:g for k,g in p.groupby(TRIP_PAIR,sort=False)}; ng={k:g for k,g in n.groupby(TRIP_PAIR,sort=False)}
    names=new_hourly.drop_duplicates(PAIR).set_index(PAIR)[["stop_a_name","stop_b_name"]]; rows=[]
    for key in pg.keys()&ng.keys():
        matched=_match_group(pg[key],ng[key])
        if len(matched)<MIN_MATCHED_TRIPS: continue
        by_hour={}
        for rec in matched: by_hour.setdefault(int(rec[0]//3600),[]).append(rec)
        for hour,recs in by_hour.items():
            if len(recs)<MIN_MATCHED_TRIPS: continue
            diffs=pd.Series([round(r[4]) for r in recs],dtype="int64"); changed=diffs[diffs.abs()>=THRESHOLD_S]
            if changed.empty: continue
            dominant=int(changed.value_counts().index[0]); supporting=[r for r in recs if round(r[4])==dominant]
            if len(supporting)/len(recs)<MIN_CHANGED_SHARE: continue
            old_s=float(pd.Series([r[2] for r in supporting]).median()); new_s=float(pd.Series([r[3] for r in supporting]).median()); date_s,line,direction,a,b=key
            try: nm=names.loc[key]; a_name,b_name=nm["stop_a_name"],nm["stop_b_name"]
            except KeyError: a_name=b_name=""
            rows.append({"detected_on":today,"date":date_s,"line":line,"direction_id":direction,"stop_a_id":a,"stop_a_name":a_name,"stop_b_id":b,"stop_b_name":b_name,"hour":hour,"old_s":old_s,"new_s":new_s,"diff_s":new_s-old_s,"kind":"time_changed"})
    return pd.DataFrame(rows,columns=CHANGE_COLS)


def compare(prev,new,today,prev_trips=None,new_trips=None):
    common=set(prev["date"])&set(new["date"])
    if not common: return pd.DataFrame(columns=CHANGE_COLS)
    p=prev[prev["date"].isin(common)]; n=new[new["date"].isin(common)]
    time_changes=compare_trip_times(prev_trips,new_trips,new,today) if prev_trips is not None and new_trips is not None else pd.DataFrame(columns=CHANGE_COLS)
    names=["stop_a_name","stop_b_name"]; po=p[PAIR+names].drop_duplicates(PAIR); pn=n[PAIR+names].drop_duplicates(PAIR)
    removed=po.merge(pn[PAIR],on=PAIR,how="left",indicator=True); removed=removed[removed["_merge"]=="left_only"].drop(columns="_merge")
    added=pn.merge(po[PAIR],on=PAIR,how="left",indicator=True); added=added[added["_merge"]=="left_only"].drop(columns="_merge")
    def pair_rows(df,kind):
        out=df.copy(); out.insert(0,"detected_on",today); out["hour"]=""; out["old_s"]=out["new_s"]=out["diff_s"]=float("nan"); out["kind"]=kind; return out[CHANGE_COLS]
    return pd.concat([time_changes[CHANGE_COLS],pair_rows(added,"segment_added"),pair_rows(removed,"segment_removed")],ignore_index=True).sort_values(["date","line","direction_id","stop_a_id","hour"],kind="stable")


def append_changes(changes):
    if not changes.empty: changes.to_csv(CHANGES,mode="a",header=not CHANGES.exists(),index=False,encoding="utf-8")


def compute_shapes(trips,today):
    sp=ts.GTFS_DIR/"shapes.txt"
    if not sp.exists() or (trips["shape_id"]=="").all(): return
    used=set(trips["shape_id"])-{""}; cols=["shape_id","shape_pt_lat","shape_pt_lon","shape_pt_sequence"]; parts=[]
    for chunk in pd.read_csv(sp,dtype=str,usecols=cols,keep_default_na=False,encoding="utf-8-sig",chunksize=500_000): parts.append(chunk[chunk["shape_id"].isin(used)])
    sh=pd.concat(parts,ignore_index=True); sh["lat"]=pd.to_numeric(sh["shape_pt_lat"]).round(5); sh["lon"]=pd.to_numeric(sh["shape_pt_lon"]).round(5); sh["seq"]=pd.to_numeric(sh["shape_pt_sequence"]); sh=sh.sort_values(["shape_id","seq"])
    id_to_hash={}; coords_by_hash={}
    for sid,g in sh.groupby("shape_id",sort=False):
        coords=[[lon,lat] for lon,lat in zip(g["lon"],g["lat"])]; h=hashlib.sha1(json.dumps(coords).encode()).hexdigest()[:12]; id_to_hash[sid]=h; coords_by_hash[h]=coords
    t=trips[trips["shape_id"].isin(id_to_hash)].copy(); t["shape_hash"]=t["shape_id"].map(id_to_hash); cur=t.groupby(["shape_hash","line","direction_id"]).size().rename("n_trips").reset_index()
    log=pd.read_csv(SHAPE_LOG,dtype=str,keep_default_na=False) if SHAPE_LOG.exists() else pd.DataFrame(columns=["shape_hash","line","direction_id","first_seen","last_seen"])
    k=["shape_hash","line","direction_id"]; merged=cur[k].merge(log,on=k,how="left"); is_new=merged["first_seen"].isna()|(merged["first_seen"]==""); new_rows=merged[is_new][k].assign(first_seen=today,last_seen=today)
    log=log.merge(cur[k].assign(_now=True),on=k,how="left"); log.loc[log["_now"].notna(),"last_seen"]=today; log=pd.concat([log.drop(columns="_now"),new_rows],ignore_index=True); log.to_csv(SHAPE_LOG,index=False,encoding="utf-8")
    SHAPES_DIR.mkdir(parents=True,exist_ok=True)
    for h in set(new_rows["shape_hash"]):
        f=SHAPES_DIR/f"{h}.json"
        if not f.exists(): f.write_text(json.dumps(coords_by_hash[h],separators=(",",":")),encoding="utf-8")


def main():
    today=date.today().isoformat(); zip_path=ts.get_feed(sys.argv); feed_hash=sha256(zip_path)
    prev_meta=json.loads(META.read_text()) if META.exists() else {}
    if prev_meta.get("feed_sha256")==feed_hash and (ts.DATA/"web"/"segments.json").exists(): print("Feed je identický s minulým během, nic se nemění."); return
    new,trips=ts.compute(zip_path); new_trips=compute_trip_state(trips); prev=load_state(); prev_trips=load_trip_state()

    qa=feed_quality.validate(new,new_trips,prev,prev_trips); qa.print()
    if not qa.ok:
        raise SystemExit("Nový GTFS neprošel kontrolou kvality. Předchozí stav zůstává beze změny.")

    changes=pd.DataFrame(columns=CHANGE_COLS) if prev is None else compare(prev,new,today,prev_trips,new_trips)
    print(f"Nalezených změn: {len(changes):,}"); append_changes(changes); compute_shapes(trips,today)
    STATE_DIR.mkdir(parents=True,exist_ok=True); new.to_csv(STATE,index=False,compression="gzip",encoding="utf-8"); new_trips.to_csv(TRIP_STATE,index=False,compression="gzip",encoding="utf-8")
    META.write_text(json.dumps({"feed_sha256":feed_hash,"updated":today,"detector":"matched-trips-v1","qa":"passed"}))
    export_web.run(zip_path)


if __name__=="__main__": main()

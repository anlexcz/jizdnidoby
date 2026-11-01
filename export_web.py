#!/usr/bin/env python3
import json
from datetime import date
import numpy as np
import pandas as pd
import tram_segments as ts
import time_regimes
import change_events
WEB=ts.DATA/"web"
def dump(o,n):(WEB/n).write_text(json.dumps(o,ensure_ascii=False,separators=(",",":")),encoding="utf-8")
def chunks(path,cols,ids,col):
    for c in pd.read_csv(path,dtype=str,usecols=cols,keep_default_na=False,encoding="utf-8-sig",chunksize=500_000): yield c[c[col].isin(ids)]
def has_col(path,col): return col in pd.read_csv(path,nrows=0,encoding="utf-8-sig").columns
def segments():
    G=ts.GTFS_DIR; routes=ts.read("routes.txt"); routes=routes[routes["route_type"].map(ts.is_tram)]; trips=ts.read("trips.txt"); trips=trips[trips["route_id"].isin(routes["route_id"])].merge(routes[["route_id","route_short_name"]],on="route_id")
    for c in ("direction_id","shape_id"):
        if c not in trips: trips[c]=""
    rep=trips.drop_duplicates(["route_short_name","direction_id","shape_id"]); sd_in_st=has_col(G/"stop_times.txt","shape_dist_traveled"); cols=["trip_id","stop_id","stop_sequence"]+(["shape_dist_traveled"] if sd_in_st else []); st=pd.concat(chunks(G/"stop_times.txt",cols,set(rep["trip_id"]),"trip_id"),ignore_index=True); st["seq"]=pd.to_numeric(st["stop_sequence"]); st=st.sort_values(["trip_id","seq"]); stg=dict(tuple(st.groupby("trip_id")))
    shape_groups={}; sp=G/"shapes.txt"
    if sp.exists():
        sdsh=has_col(sp,"shape_dist_traveled"); sc=["shape_id","shape_pt_lat","shape_pt_lon","shape_pt_sequence"]+(["shape_dist_traveled"] if sdsh else []); shp=pd.concat(chunks(sp,sc,set(rep["shape_id"])-{""},"shape_id"),ignore_index=True); shp["seq"]=pd.to_numeric(shp["shape_pt_sequence"]); shape_groups={k:g.sort_values("seq") for k,g in shp.groupby("shape_id")}
    stops=ts.read("stops.txt",usecols=["stop_id","stop_name","stop_lat","stop_lon"]).drop_duplicates("stop_id").set_index("stop_id"); out={}; straight=0
    for r in rep.itertuples():
        g=stg.get(r.trip_id)
        if g is None: continue
        lonlat=sd=None; sg=shape_groups.get(r.shape_id)
        if sg is not None:
            lonlat=np.column_stack([pd.to_numeric(sg["shape_pt_lon"]),pd.to_numeric(sg["shape_pt_lat"])])
            if "shape_dist_traveled" in sg and "shape_dist_traveled" in g:
                sd=pd.to_numeric(sg["shape_dist_traveled"],errors="coerce").to_numpy()
                if np.isnan(sd).any() or (np.diff(sd)<0).any(): sd=None
        rr=list(g.itertuples())
        for i,(a,b) in enumerate(zip(rr,rr[1:])):
            k=f"{r.route_short_name}|{r.direction_id}|{a.stop_id}|{b.stop_id}"
            if k in out or a.stop_id not in stops.index or b.stop_id not in stops.index: continue
            geom=None
            if sd is not None:
                da=pd.to_numeric(a.shape_dist_traveled,errors="coerce"); db=pd.to_numeric(b.shape_dist_traveled,errors="coerce")
                if pd.notna(da) and pd.notna(db) and db>da:
                    x,y=np.searchsorted(sd,da-.5,"left"),np.searchsorted(sd,db+.5,"right")
                    if y-x>=2: geom=lonlat[x:y]
            if geom is None: straight+=1; geom=np.array([[float(stops.at[s,"stop_lon"]),float(stops.at[s,"stop_lat"])] for s in (a.stop_id,b.stop_id)])
            out[k]={"k":k,"l":r.route_short_name,"d":r.direction_id,"i":i,"n":[stops.at[a.stop_id,"stop_name"],stops.at[b.stop_id,"stop_name"]],"g":np.round(geom,5).tolist()}
    print(f"Geometrie úseků: {len(out)} (z toho {straight} přímou čarou)"); return [out[k] for k in sorted(out)]
def current():
    p=ts.DATA/"state"/"state.csv.gz"
    if not p.exists(): return {"dates":{},"v":{}}
    df=pd.read_csv(p,dtype=str,keep_default_na=False); df["hour"]=df["hour"].astype(int); df["m"]=df["median_s"].astype(float).round().astype(int); df["wd"]=pd.to_datetime(df["date"]).dt.weekday; today=date.today().isoformat(); dates={}; v={}
    for t,f in {"w":lambda x:x<5,"s":lambda x:x==5,"n":lambda x:x==6}.items():
        sub=df[df["wd"].map(f)]
        if sub.empty: continue
        future=sub[sub["date"]>=today]; d=sorted((future if len(future) else sub)["date"].unique())[0]; dates[t]=d
        for r in sub[sub["date"]==d].itertuples():
            if r.hour<29: v.setdefault(f"{r.line}|{r.direction_id}|{r.stop_a_id}|{r.stop_b_id}",{}).setdefault(t,[0]*29)[r.hour]=r.m
    return {"dates":dates,"v":v}
def _changes_df():
    p=ts.DATA/"changes.csv"; df=pd.read_csv(p,dtype=str,keep_default_na=False) if p.exists() else pd.DataFrame(); ep=ts.DATA/"evidence.csv"
    if not df.empty and ep.exists():
        ev=pd.read_csv(ep,dtype=str,keep_default_na=False); keys=["detected_on","date","line","direction_id","stop_a_id","stop_b_id","hour"]; df=df.merge(ev,on=keys,how="left")
    return df
def changes(df=None):
    df=_changes_df() if df is None else df
    if df.empty:return []
    out=[]; keys=["detected_on","line","direction_id","stop_a_id","stop_b_id","kind"]
    for (on,l,d,a,b,k),g in df.groupby(keys,sort=True):
        r={"on":on,"l":l,"d":d,"a":a,"b":b,"t":k,"f":g["date"].min(),"u":g["date"].max(),"n":[g["stop_a_name"].iloc[0],g["stop_b_name"].iloc[0]]}
        if k=="time_changed":
            z=g.assign(x=pd.to_numeric(g["diff_s"])); top=z.loc[z["x"].abs().idxmax()]; r.update(x=int(round(top.x)),o=int(round(float(top.old_s))),w=int(round(float(top.new_s))),h=sorted({int(h) for h in g["hour"]}))
        out.append(r)
    return out
def regimes_df(df=None): return time_regimes.build_validity_from_changes(_changes_df() if df is None else df)
def regimes(df=None):
    out=[]
    for r in regimes_df(df).itertuples(index=False):
        share=None if pd.isna(r.support_share) else round(float(r.support_share),3); out.append({"on":r.detected_on,"l":r.line,"d":r.direction_id,"a":r.stop_a_id,"b":r.stop_b_id,"n":[r.stop_a_name,r.stop_b_name],"s":int(r.start_hour),"e":int(r.end_hour),"o":int(round(float(r.old_s))),"w":int(round(float(r.new_s))),"x":int(round(float(r.diff_s))),"v":r.validity,"f":r.valid_from,"u":r.valid_to,"dates":r.dates.split(",") if r.dates else [],"evidence":{"matched":int(r.matched_trips),"supporting":int(r.supporting_trips),"share":share}})
    return out
def events(df=None): return change_events.build(regimes_df(df))
def shapes():
    p=ts.DATA/"shape_log.csv"
    if not p.exists():return []
    df=pd.read_csv(p,dtype=str,keep_default_na=False); return [{"l":r.line,"d":r.direction_id,"h":r.shape_hash,"first":r.first_seen,"last":r.last_seen} for r in df.itertuples()]
def run(zip_path):
    ts.unzip(zip_path); WEB.mkdir(parents=True,exist_ok=True); dump(segments(),"segments.json"); dump(current(),"current.json"); df=_changes_df(); ch=changes(df); rg=regimes(df); ev=events(df); dump(ch,"changes.json"); dump(rg,"regimes.json"); dump(ev,"events.json"); dump(shapes(),"shapes.json"); print(f"Web data: {len(ch)} změn, {len(rg)} režimů, {len(ev)} událostí")
if __name__=="__main__":
    import sys; run(ts.get_feed(sys.argv))

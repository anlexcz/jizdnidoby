#!/usr/bin/env python3
"""V3 wrapper: zachová kompatibilní changes.csv a vedle něj ukládá důkazy detekce."""
import pandas as pd
import daily_update as du

EVIDENCE=du.ts.DATA/"evidence.csv"
EVIDENCE_COLS=["detected_on","date","line","direction_id","stop_a_id","stop_b_id","hour","matched_trips","supporting_trips","support_share","diff_min_s","diff_max_s"]


def _append(rows):
    if not rows: return
    df=pd.DataFrame(rows,columns=EVIDENCE_COLS)
    if EVIDENCE.exists():
        old=pd.read_csv(EVIDENCE,dtype=str,keep_default_na=False); df=pd.concat([old,df.astype(str)],ignore_index=True)
        df=df.drop_duplicates(["detected_on","date","line","direction_id","stop_a_id","stop_b_id","hour"],keep="last")
    df.to_csv(EVIDENCE,index=False,encoding="utf-8")


def compare_trip_times(prev_trips,new_trips,new_hourly,today):
    common=set(prev_trips["date"]) & set(new_trips["date"])
    if not common: return pd.DataFrame(columns=du.CHANGE_COLS)
    p=prev_trips[prev_trips["date"].isin(common)]; n=new_trips[new_trips["date"].isin(common)]
    pg={k:g for k,g in p.groupby(du.TRIP_PAIR,sort=False)}; ng={k:g for k,g in n.groupby(du.TRIP_PAIR,sort=False)}
    names=new_hourly.drop_duplicates(du.PAIR).set_index(du.PAIR)[["stop_a_name","stop_b_name"]]; rows=[]; evidence=[]
    for key in pg.keys() & ng.keys():
        matched=du._match_group(pg[key],ng[key])
        if len(matched)<du.MIN_MATCHED_TRIPS: continue
        by_hour={}
        for rec in matched: by_hour.setdefault(int(rec[0]//3600),[]).append(rec)
        for hour,recs in by_hour.items():
            if len(recs)<du.MIN_MATCHED_TRIPS: continue
            diffs=pd.Series([round(r[4]) for r in recs],dtype="int64"); changed=diffs[diffs.abs()>=du.THRESHOLD_S]
            if changed.empty: continue
            dominant=int(changed.value_counts().index[0]); supporting=[r for r in recs if round(r[4])==dominant]; share=len(supporting)/len(recs)
            if share<du.MIN_CHANGED_SHARE: continue
            date_s,line,direction,a_key,b_key=key; cur=ng[key]; a=cur["stop_a_id"].mode().iloc[0]; b=cur["stop_b_id"].mode().iloc[0]
            try: nm=names.loc[(date_s,line,direction,a,b)]; an,bn=nm["stop_a_name"],nm["stop_b_name"]
            except KeyError: an=bn=""
            old=float(pd.Series([r[2] for r in supporting]).median()); new=float(pd.Series([r[3] for r in supporting]).median())
            rows.append({"detected_on":today,"date":date_s,"line":line,"direction_id":direction,"stop_a_id":a,"stop_a_name":an,"stop_b_id":b,"stop_b_name":bn,"hour":hour,"old_s":old,"new_s":new,"diff_s":new-old,"kind":"time_changed"})
            evidence.append({"detected_on":today,"date":date_s,"line":line,"direction_id":direction,"stop_a_id":a,"stop_b_id":b,"hour":hour,"matched_trips":len(recs),"supporting_trips":len(supporting),"support_share":share,"diff_min_s":int(diffs.min()),"diff_max_s":int(diffs.max())})
    _append(evidence)
    return pd.DataFrame(rows,columns=du.CHANGE_COLS)


du.compare_trip_times=compare_trip_times
if __name__=="__main__": du.main()

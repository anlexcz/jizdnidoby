"""Seskupení potvrzených hodinových změn do časových režimů a kalendářní platnosti."""
import pandas as pd

EVIDENCE=["matched_trips","supporting_trips","support_share","diff_min_s","diff_max_s"]
REGIME_COLS=["detected_on","date","line","direction_id","stop_a_id","stop_a_name","stop_b_id","stop_b_name","start_hour","end_hour","old_s","new_s","diff_s"]+EVIDENCE
GROUP=["detected_on","date","line","direction_id","stop_a_id","stop_a_name","stop_b_id","stop_b_name"]
VALIDITY_COLS=["detected_on","line","direction_id","stop_a_id","stop_a_name","stop_b_id","stop_b_name","start_hour","end_hour","old_s","new_s","diff_s","validity","valid_from","valid_to","dates"]+EVIDENCE


def build(changes):
    if changes is None or changes.empty or "kind" not in changes: return pd.DataFrame(columns=REGIME_COLS)
    x=changes[changes["kind"]=="time_changed"].copy()
    if x.empty: return pd.DataFrame(columns=REGIME_COLS)
    for c in ("hour","old_s","new_s","diff_s")+tuple(EVIDENCE):
        if c not in x: x[c]=""
        x[c]=pd.to_numeric(x[c],errors="coerce")
    x=x.dropna(subset=["hour","old_s","new_s","diff_s"]); x["hour"]=x["hour"].astype(int); x["diff_key"]=x["diff_s"].round().astype(int); rows=[]
    for key,g in x.groupby(GROUP,sort=False,dropna=False):
        run=[]
        for r in g.sort_values("hour").itertuples(index=False):
            if run and (r.hour!=run[-1].hour+1 or r.diff_key!=run[-1].diff_key): rows.append(_row(key,run)); run=[]
            run.append(r)
        if run: rows.append(_row(key,run))
    return pd.DataFrame(rows,columns=REGIME_COLS)


def _row(key,run):
    old=float(pd.Series([r.old_s for r in run]).median()); new=float(pd.Series([r.new_s for r in run]).median()); out=dict(zip(GROUP,key))
    matched=sum(int(r.matched_trips) for r in run if pd.notna(r.matched_trips)); supporting=sum(int(r.supporting_trips) for r in run if pd.notna(r.supporting_trips))
    mins=[r.diff_min_s for r in run if pd.notna(r.diff_min_s)]; maxs=[r.diff_max_s for r in run if pd.notna(r.diff_max_s)]
    out.update(start_hour=int(run[0].hour),end_hour=int(run[-1].hour)+1,old_s=old,new_s=new,diff_s=new-old,matched_trips=matched,supporting_trips=supporting,support_share=(supporting/matched if matched else float("nan")),diff_min_s=min(mins) if mins else float("nan"),diff_max_s=max(maxs) if maxs else float("nan")); return out


def _validity(dates):
    ds=sorted(pd.to_datetime(list(dates))); w={d.weekday() for d in ds}
    if not ds: return "dates"
    span=pd.date_range(ds[0],ds[-1],freq="D"); present={d.date() for d in ds}
    complete=lambda wanted: all((d.weekday() not in wanted) or (d.date() in present) for d in span)
    if w==set(range(7)) and len(ds)==len(span): return "daily"
    if w<=set(range(5)) and complete(set(range(5))): return "weekdays"
    if w<={5,6} and complete({5,6}): return "weekend"
    if w=={5} and complete({5}): return "saturday"
    if w=={6} and complete({6}): return "sunday"
    return "dates"


def build_validity(regimes):
    if regimes is None or regimes.empty: return pd.DataFrame(columns=VALIDITY_COLS)
    base=["detected_on","line","direction_id","stop_a_id","stop_a_name","stop_b_id","stop_b_name","start_hour","end_hour","old_s","new_s","diff_s"]
    rows=[]
    for key,g in regimes.groupby(base,sort=False,dropna=False):
        dates=sorted(set(g["date"].astype(str))); out=dict(zip(base,key)); matched=int(pd.to_numeric(g["matched_trips"],errors="coerce").fillna(0).sum()); supporting=int(pd.to_numeric(g["supporting_trips"],errors="coerce").fillna(0).sum())
        mn=pd.to_numeric(g["diff_min_s"],errors="coerce"); mx=pd.to_numeric(g["diff_max_s"],errors="coerce")
        out.update(validity=_validity(dates),valid_from=dates[0],valid_to=dates[-1],dates=",".join(dates),matched_trips=matched,supporting_trips=supporting,support_share=(supporting/matched if matched else float("nan")),diff_min_s=mn.min() if mn.notna().any() else float("nan"),diff_max_s=mx.max() if mx.notna().any() else float("nan")); rows.append(out)
    return pd.DataFrame(rows,columns=VALIDITY_COLS)


def build_validity_from_changes(changes): return build_validity(build(changes))

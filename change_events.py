"""Skládání časových režimů do uživatelsky čitelných událostí změn."""
import pandas as pd

def build(regimes: pd.DataFrame) -> list[dict]:
    if regimes is None or regimes.empty:return []
    group=["detected_on","line","direction_id","start_hour","end_hour","diff_s","validity","valid_from","valid_to","dates"]; out=[]
    for key,g in regimes.groupby(group,sort=False,dropna=False):
        unused=g.to_dict("records")
        while unused:
            ends={r["stop_b_id"] for r in unused}; first=next((r for r in unused if r["stop_a_id"] not in ends),unused[0]); chain=[first]; unused.remove(first)
            while True:
                nxt=next((r for r in unused if r["stop_a_id"]==chain[-1]["stop_b_id"]),None)
                if nxt is None:break
                chain.append(nxt); unused.remove(nxt)
            k=dict(zip(group,key)); matched=sum(int(r.get("matched_trips",0) or 0) for r in chain); supporting=sum(int(r.get("supporting_trips",0) or 0) for r in chain); old=sum(float(r["old_s"]) for r in chain); new=sum(float(r["new_s"]) for r in chain)
            out.append({**k,"old_s":old,"new_s":new,"total_diff_s":new-old,"stop_a_id":chain[0]["stop_a_id"],"stop_a_name":chain[0]["stop_a_name"],"stop_b_id":chain[-1]["stop_b_id"],"stop_b_name":chain[-1]["stop_b_name"],"segments":[{"a":r["stop_a_id"],"an":r["stop_a_name"],"b":r["stop_b_id"],"bn":r["stop_b_name"],"old_s":float(r["old_s"]),"new_s":float(r["new_s"]),"diff_s":float(r["diff_s"])} for r in chain],"matched_trips":matched,"supporting_trips":supporting,"confidence":supporting/matched if matched else None})
    return out

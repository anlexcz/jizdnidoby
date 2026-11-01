"""Skládání časových režimů do uživatelsky čitelných událostí změn."""
import pandas as pd


def build(regimes: pd.DataFrame) -> list[dict]:
    if regimes is None or regimes.empty:
        return []
    x = regimes.copy()
    group = ["detected_on","line","direction_id","start_hour","end_hour","old_s","new_s","diff_s","validity","valid_from","valid_to","dates"]
    out=[]
    for key,g in x.groupby(group,sort=False,dropna=False):
        # Řetězec vzniká jen z navazujících úseků; pořadí je odvozeno z A->B vazeb.
        unused=[r for r in g.to_dict("records")]
        while unused:
            starts={r["stop_a_id"] for r in unused}; ends={r["stop_b_id"] for r in unused}
            first=next((r for r in unused if r["stop_a_id"] not in ends),unused[0])
            chain=[first]; unused.remove(first)
            while True:
                nxt=next((r for r in unused if r["stop_a_id"]==chain[-1]["stop_b_id"]),None)
                if nxt is None: break
                chain.append(nxt); unused.remove(nxt)
            k=dict(zip(group,key)); matched=sum(int(r.get("matched_trips",0) or 0) for r in chain); supporting=sum(int(r.get("supporting_trips",0) or 0) for r in chain)
            confidence=(supporting/matched) if matched else None
            out.append({**k,"stop_a_id":chain[0]["stop_a_id"],"stop_a_name":chain[0]["stop_a_name"],"stop_b_id":chain[-1]["stop_b_id"],"stop_b_name":chain[-1]["stop_b_name"],"segments":[{"a":r["stop_a_id"],"an":r["stop_a_name"],"b":r["stop_b_id"],"bn":r["stop_b_name"]} for r in chain],"matched_trips":matched,"supporting_trips":supporting,"confidence":confidence})
    return out

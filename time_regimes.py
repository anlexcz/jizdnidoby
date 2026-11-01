"""Seskupení potvrzených hodinových změn jízdních dob do souvislých režimů.

Detektor zůstává konzervativní po hodinách. Tato vrstva pouze spojí sousední
potvrzené hodiny se stejnou změnou do intervalu [start_hour, end_hour).
"""
import pandas as pd

REGIME_COLS = [
    "detected_on", "date", "line", "direction_id", "stop_a_id", "stop_a_name",
    "stop_b_id", "stop_b_name", "start_hour", "end_hour", "old_s", "new_s", "diff_s",
]
GROUP = ["detected_on", "date", "line", "direction_id", "stop_a_id", "stop_a_name", "stop_b_id", "stop_b_name"]


def build(changes: pd.DataFrame) -> pd.DataFrame:
    """Vrátí maximální souvislé časové režimy z potvrzených time_changed řádků."""
    if changes is None or changes.empty or "kind" not in changes:
        return pd.DataFrame(columns=REGIME_COLS)
    x = changes[changes["kind"] == "time_changed"].copy()
    if x.empty:
        return pd.DataFrame(columns=REGIME_COLS)
    for c in ("hour", "old_s", "new_s", "diff_s"):
        x[c] = pd.to_numeric(x[c], errors="coerce")
    x = x.dropna(subset=["hour", "old_s", "new_s", "diff_s"])
    x["hour"] = x["hour"].astype(int)
    x["diff_key"] = x["diff_s"].round().astype(int)
    rows = []
    for key, g in x.groupby(GROUP, sort=False, dropna=False):
        g = g.sort_values("hour")
        run = []
        for r in g.itertuples(index=False):
            if run and (r.hour != run[-1].hour + 1 or r.diff_key != run[-1].diff_key):
                rows.append(_row(key, run)); run = []
            run.append(r)
        if run: rows.append(_row(key, run))
    return pd.DataFrame(rows, columns=REGIME_COLS)


def _row(key, run):
    old_s = float(pd.Series([r.old_s for r in run]).median())
    new_s = float(pd.Series([r.new_s for r in run]).median())
    out = dict(zip(GROUP, key))
    out.update(start_hour=int(run[0].hour), end_hour=int(run[-1].hour) + 1,
               old_s=old_s, new_s=new_s, diff_s=new_s-old_s)
    return out


def append(path, changes: pd.DataFrame) -> int:
    regimes = build(changes)
    if regimes.empty: return 0
    # Stejný feed se běžně nezpracuje dvakrát, přesto zápis držíme idempotentní.
    if path.exists():
        old = pd.read_csv(path, dtype=str, keep_default_na=False)
        combined = pd.concat([old, regimes.astype(str)], ignore_index=True)
        key = ["detected_on", "date", "line", "direction_id", "stop_a_id", "stop_b_id", "start_hour", "end_hour", "diff_s"]
        combined = combined.drop_duplicates(key, keep="last")
        combined.to_csv(path, index=False, encoding="utf-8")
    else:
        regimes.to_csv(path, index=False, encoding="utf-8")
    return len(regimes)

"""Seskupení potvrzených změn jízdních dob do časových a kalendářních režimů.

Nejdřív vzniknou souvislé hodinové režimy pro jednotlivé datumy. Potom se
stejný režim seskupí podle dnů, ve kterých byl skutečně potvrzen. Výstup tak
umí rozlišit pracovní dny, soboty, neděle, víkendy, denní provoz i nepravidelný
výběr konkrétních dnů bez toho, abychom si platnost domýšleli.
"""
import pandas as pd

REGIME_COLS = [
    "detected_on", "date", "line", "direction_id", "stop_a_id", "stop_a_name",
    "stop_b_id", "stop_b_name", "start_hour", "end_hour", "old_s", "new_s", "diff_s",
]
GROUP = ["detected_on", "date", "line", "direction_id", "stop_a_id", "stop_a_name", "stop_b_id", "stop_b_name"]
VALIDITY_COLS = [
    "detected_on", "line", "direction_id", "stop_a_id", "stop_a_name", "stop_b_id", "stop_b_name",
    "start_hour", "end_hour", "old_s", "new_s", "diff_s", "validity", "valid_from", "valid_to", "dates",
]
VALIDITY_GROUP = [
    "detected_on", "line", "direction_id", "stop_a_id", "stop_a_name", "stop_b_id", "stop_b_name",
    "start_hour", "end_hour", "old_s", "new_s", "diff_s",
]


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


def _validity_label(dates):
    """Popíše pouze takový kalendářní vzor, který data opravdu pokrývají."""
    ds = sorted(pd.to_datetime(pd.Series(dates)).dt.normalize().unique())
    if not ds: return "dates"
    weekdays = {pd.Timestamp(d).weekday() for d in ds}
    first, last = pd.Timestamp(ds[0]), pd.Timestamp(ds[-1])
    calendar = pd.date_range(first, last, freq="D")
    patterns = [
        ("daily", set(range(7))),
        ("weekdays", set(range(5))),
        ("weekend", {5, 6}),
        ("saturday", {5}),
        ("sunday", {6}),
    ]
    actual = {pd.Timestamp(d) for d in ds}
    for label, allowed in patterns:
        expected = {d for d in calendar if d.weekday() in allowed}
        if expected and actual == expected:
            return label
    return "dates"


def build_validity(regimes: pd.DataFrame) -> pd.DataFrame:
    """Sloučí stejné časové režimy přes datumy a odvodí jejich typ platnosti."""
    if regimes is None or regimes.empty:
        return pd.DataFrame(columns=VALIDITY_COLS)
    x = regimes.copy()
    for c in ("start_hour", "end_hour", "old_s", "new_s", "diff_s"):
        x[c] = pd.to_numeric(x[c], errors="coerce")
    x = x.dropna(subset=["date", "start_hour", "end_hour", "old_s", "new_s", "diff_s"])
    rows = []
    for key, g in x.groupby(VALIDITY_GROUP, sort=False, dropna=False):
        dates = sorted(set(g["date"].astype(str)))
        out = dict(zip(VALIDITY_GROUP, key))
        out.update(validity=_validity_label(dates), valid_from=dates[0], valid_to=dates[-1], dates=",".join(dates))
        rows.append(out)
    return pd.DataFrame(rows, columns=VALIDITY_COLS)


def build_validity_from_changes(changes: pd.DataFrame) -> pd.DataFrame:
    return build_validity(build(changes))


def append(path, changes: pd.DataFrame) -> int:
    regimes = build(changes)
    if regimes.empty: return 0
    if path.exists():
        old = pd.read_csv(path, dtype=str, keep_default_na=False)
        combined = pd.concat([old, regimes.astype(str)], ignore_index=True)
        key = ["detected_on", "date", "line", "direction_id", "stop_a_id", "stop_b_id", "start_hour", "end_hour", "diff_s"]
        combined = combined.drop_duplicates(key, keep="last")
        combined.to_csv(path, index=False, encoding="utf-8")
    else:
        regimes.to_csv(path, index=False, encoding="utf-8")
    return len(regimes)

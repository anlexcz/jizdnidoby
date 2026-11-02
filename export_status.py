#!/usr/bin/env python3
import csv
import json
from datetime import date, timedelta
from pathlib import Path

DATA = Path("data")
WEB = DATA / "web"
RUN_LOG = DATA / "run_log.csv"
META = DATA / "state" / "meta.json"
CURRENT = WEB / "current.json"


def load_runs():
    if not RUN_LOG.exists():
        return []
    with RUN_LOG.open(encoding="utf-8") as f:
        return sorted({row["date"] for row in csv.DictReader(f) if row.get("date")})


def main():
    WEB.mkdir(parents=True, exist_ok=True)
    today = date.today()
    runs = load_runs()
    meta = json.loads(META.read_text(encoding="utf-8")) if META.exists() else {}
    current = json.loads(CURRENT.read_text(encoding="utf-8")) if CURRENT.exists() else {"dates": {}}

    window_start = today - timedelta(days=6)
    tracking_since = date.fromisoformat(runs[0]) if runs else today
    effective_start = max(window_start, tracking_since)
    full_window = tracking_since <= window_start
    expected_dates = []
    cursor = effective_start
    while cursor <= today:
        expected_dates.append(cursor.isoformat())
        cursor += timedelta(days=1)

    ok_days = [d for d in expected_dates if d in runs]
    missing = [d for d in expected_dates if d not in runs]
    status = {
        "generated": today.isoformat(),
        "feed_updated": meta.get("updated"),
        "qa": meta.get("qa"),
        "detector": meta.get("detector"),
        "current_dates": current.get("dates", {}),
        "last_success": runs[-1] if runs else None,
        "tracking_since": tracking_since.isoformat(),
        "week": {
            "ok": len(ok_days),
            "expected": len(expected_dates),
            "full_window": full_window,
            "days": [{"date": d, "ok": d in runs} for d in expected_dates],
            "missing": missing,
        },
    }
    (WEB / "status.json").write_text(
        json.dumps(status, ensure_ascii=False, separators=(",", ":")), encoding="utf-8"
    )


if __name__ == "__main__":
    main()

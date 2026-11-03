#!/usr/bin/env python3
"""Fail closed before publishing a newly computed state.

This validation intentionally does not rewrite or migrate historical data. It only
checks that the state and public web export are internally usable before the
workflow uploads the state release and commits generated files.
"""
from __future__ import annotations

import csv
import json
from pathlib import Path

import pandas as pd

DATA = Path("data")
STATE = DATA / "state"
WEB = DATA / "web"


def require(path: Path) -> Path:
    if not path.exists() or path.stat().st_size == 0:
        raise SystemExit(f"Chybí povinný výstup: {path}")
    return path


def read_json(path: Path):
    require(path)
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise SystemExit(f"Neplatný JSON {path}: {exc}") from exc


def validate_state() -> None:
    state = pd.read_csv(require(STATE / "state.csv.gz"), compression="gzip")
    trips = pd.read_csv(require(STATE / "trip_state.csv.gz"), compression="gzip")
    meta = read_json(STATE / "meta.json")

    state_cols = {"date", "line", "direction_id", "stop_a_id", "stop_b_id", "hour", "median_s", "n_trips"}
    trip_cols = {"date", "line", "direction_id", "stop_a_id", "stop_b_id", "stop_a_key", "stop_b_key", "dep_s", "dur_s"}
    if not state_cols.issubset(state.columns):
        raise SystemExit(f"state.csv.gz nemá očekávaná pole: {sorted(state_cols - set(state.columns))}")
    if not trip_cols.issubset(trips.columns):
        raise SystemExit(f"trip_state.csv.gz nemá očekávaná pole: {sorted(trip_cols - set(trips.columns))}")
    if state.empty or trips.empty:
        raise SystemExit("Nový stav je prázdný; odmítám ho publikovat.")
    if meta.get("qa") != "passed":
        raise SystemExit("meta.json nepotvrzuje úspěšné QA.")
    if not meta.get("feed_sha256") or not meta.get("updated"):
        raise SystemExit("meta.json neobsahuje identitu feedu nebo datum aktualizace.")


def validate_web() -> None:
    current = read_json(WEB / "current.json")
    segments = read_json(WEB / "segments.json")
    for name in ("changes.json", "events.json", "regimes.json", "shapes.json"):
        value = read_json(WEB / name)
        if not isinstance(value, list):
            raise SystemExit(f"{name} musí být JSON pole.")

    if not isinstance(segments, list) or not segments:
        raise SystemExit("segments.json je prázdný nebo nemá očekávaný formát.")
    if not isinstance(current, dict) or not isinstance(current.get("v"), dict):
        raise SystemExit("current.json nemá očekávaný formát.")

    # Historické geometrie jsou append-only archiv. Záznam v logu nesmí ukazovat
    # na neexistující soubor; validátor ale žádný starý soubor nemaže ani nepřepisuje.
    shape_log = DATA / "shape_log.csv"
    if shape_log.exists():
        with shape_log.open(encoding="utf-8", newline="") as handle:
            for row in csv.DictReader(handle):
                shape_hash = (row.get("shape_hash") or "").strip()
                if shape_hash and not (DATA / "shapes" / f"{shape_hash}.json").exists():
                    raise SystemExit(f"shape_log.csv odkazuje na chybějící geometrii {shape_hash}.json")


def main() -> None:
    validate_state()
    validate_web()
    print("Validace nového stavu a webových dat: OK")


if __name__ == "__main__":
    main()

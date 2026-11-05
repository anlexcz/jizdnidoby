#!/usr/bin/env python3
"""Fail closed before publishing a newly computed state.

The validator never migrates or rewrites historical evidence. It checks the new
working snapshot and derived web export before the workflow uploads state or
commits generated data.
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


def validate_segment_shards(legacy_segments: list) -> None:
    index = read_json(WEB / "segment_index.json")
    if index.get("version") != 1 or not isinstance(index.get("lines"), dict):
        raise SystemExit("segment_index.json nemá podporovaný formát.")

    total = 0
    seen_keys = set()
    for line, entry in index["lines"].items():
        if not isinstance(entry, dict) or not entry.get("file"):
            raise SystemExit(f"Neplatný záznam geometrie linky {line}.")
        shard_path = WEB / entry["file"]
        shard = read_json(shard_path)
        if not isinstance(shard, list) or not shard:
            raise SystemExit(f"Shard geometrie linky {line} je prázdný.")
        if entry.get("count") != len(shard):
            raise SystemExit(f"Nesedí počet úseků v geometrickém shardu linky {line}.")
        for segment in shard:
            if str(segment.get("l", "")) != str(line):
                raise SystemExit(f"Shard linky {line} obsahuje úsek jiné linky.")
            segment_key = segment.get("k")
            if not segment_key or segment_key in seen_keys:
                raise SystemExit(f"Duplicitní nebo chybějící klíč úseku v shardu linky {line}.")
            seen_keys.add(segment_key)
        total += len(shard)

    if total != len(legacy_segments) or index.get("count") != total:
        raise SystemExit(
            f"Shardy geometrie nejsou úplné: shardy={total}, legacy={len(legacy_segments)}, index={index.get('count')}"
        )


def validate_variants_v3() -> None:
    index = read_json(WEB / "timetable_variants_v3_index.json")
    if index.get("version") != 3 or not isinstance(index.get("lines"), dict):
        raise SystemExit("Index variant tras v3 nema podporovany format.")
    if not index["lines"]:
        raise SystemExit("V exportu variant tras v3 chybi linky.")
    for line, entry in index["lines"].items():
        if not str(line).isdigit() or not 1 <= int(line) <= 37:
            raise SystemExit(f"V exportu v3 je neplatna denni linka: {line}.")
        payload = read_json(WEB / entry["file"])
        if payload.get("version") != 3 or str(payload.get("line")) != str(line):
            raise SystemExit(f"Nesouhlasi identita exportu v3 pro linku {line}.")
        for date, directions in payload.get("dates", {}).items():
            for direction, value in directions.items():
                seen = set()
                for variant in value.get("variants", []):
                    variant_id = variant.get("id")
                    stops = variant.get("stops", [])
                    if not variant_id or variant_id in seen or len(stops) < 2:
                        raise SystemExit(f"Vadna varianta {line}/{date}/{direction}.")
                    seen.add(variant_id)
                    if variant.get("classification") not in {"regular_base", "regular_day_variant"}:
                        raise SystemExit(f"Neregularni spoj v hlavnim exportu {line}/{date}/{direction}.")
                    if variant.get("trip_count", 0) <= 0:
                        raise SystemExit(f"Varianta bez spoju {line}/{date}/{direction}.")
                    for hour, seconds in variant.get("hours", {}).items():
                        if len(seconds) != len(stops) - 1:
                            raise SystemExit(f"Nesouhlasi pocet useku {line}/{date}/{direction}/{hour}.")


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
    validate_segment_shards(segments)
    validate_variants_v3()

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

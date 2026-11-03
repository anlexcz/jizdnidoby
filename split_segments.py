#!/usr/bin/env python3
"""Split the legacy geometry export into small line-scoped files.

`segments.json` remains untouched as a compatibility snapshot. The frontend uses
`segment_index.json` + `segments/*.json` when available and falls back to the
legacy file before the first rebuilt data run.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

WEB = Path("data/web")
SOURCE = WEB / "segments.json"
TARGET = WEB / "segments"
INDEX = WEB / "segment_index.json"


def dump(path: Path, value) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )


def main() -> None:
    if not SOURCE.exists():
        raise SystemExit("Chybí data/web/segments.json")
    segments = json.loads(SOURCE.read_text(encoding="utf-8"))
    if not isinstance(segments, list) or not segments:
        raise SystemExit("segments.json je prázdný nebo neplatný")

    grouped: dict[str, list] = {}
    for segment in segments:
        line = str(segment.get("l", ""))
        if not line:
            raise SystemExit("Úsek bez čísla linky v segments.json")
        grouped.setdefault(line, []).append(segment)

    TARGET.mkdir(parents=True, exist_ok=True)
    manifest = {"version": 1, "count": len(segments), "lines": {}}
    expected_files = set()
    for line in sorted(grouped, key=lambda value: (not value.isdigit(), int(value) if value.isdigit() else value)):
        filename = f"{hashlib.sha1(line.encode('utf-8')).hexdigest()[:12]}.json"
        expected_files.add(filename)
        dump(TARGET / filename, grouped[line])
        manifest["lines"][line] = {"file": f"segments/{filename}", "count": len(grouped[line])}

    # Smažeme pouze staré *odvozené shardy*. Historický zdroj segments.json ani
    # žádná evidenční data tímto skriptem dotčena nejsou.
    for path in TARGET.glob("*.json"):
        if path.name not in expected_files:
            path.unlink()

    dump(INDEX, manifest)
    print(f"Geometrie rozdělena: {len(segments)} úseků / {len(grouped)} linek")


if __name__ == "__main__":
    main()

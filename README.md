# Jízdní doby

Sledování změn mezizastávkových jízdních dob pražských tramvají nad PID GTFS.

## Architektura

Projekt má tři oddělené vrstvy:

1. **Historická evidence v Git repozitáři** – `data/changes.csv`, `data/evidence.csv`, `data/shape_log.csv` a `data/shapes/`. Tato data jsou append-only a představují historii projektu.
2. **Pracovní snapshot v GitHub Release `state`** – `state.csv.gz`, `trip_state.csv.gz`, `meta.json`. Snapshot slouží pouze jako výchozí bod příštího porovnání a může být nahrazen až po úspěšném QA a validaci.
3. **Odvozená webová data** – `data/web/`. Mohou se kdykoliv znovu vytvořit z pracovního snapshotu a historie. `segments.json` zůstává jako kompatibilní export; frontend používá menší shardy přes `segment_index.json` a `data/web/segments/*.json`.

## Denní pipeline

`daily_update.py` je jediný vstupní bod backendu. Stáhne GTFS, vytvoří nový snapshot, provede QA, porovná ho s předchozím stavem, doplní změny a důkazy detekce a vytvoří webový export. `split_segments.py` rozdělí mapovou geometrii po linkách. `validate_build.py` kontroluje snapshot, webová data, shardy geometrie a vazby historického archivu ještě před tím, než workflow nový stav publikuje.

Workflow má primární a záložní denní cron. Duplicitní úspěšný běh je bezpečný: identický feed se znovu neporovnává a `run_log.csv` obsahuje datum pouze jednou.

## Frontend

Frontend je statický web pro GitHub Pages. Změny a stav dat se načtou bez kompletní geometrie sítě. Geometrie se dotahuje po jednotlivých linkách podle potřeby; při prvním přechodném nasazení umí frontend použít i původní `segments.json`. Chyba načtení dat se zobrazuje uživateli místo tichého prázdného stavu.

## Kontroly

Na každý push a pull request se spouští syntax check Pythonu a JavaScriptu a unit testy. Větev `clean-rebuild` navíc spouští read-only smoke test celé pipeline proti aktuálnímu produkčnímu snapshotu a aktuálnímu GTFS. Smoke test nic nenahrává do release a nic necommituje.

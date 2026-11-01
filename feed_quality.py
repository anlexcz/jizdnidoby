"""Kontroly kvality vypočteného tramvajového GTFS před přijetím nového stavu."""
from dataclasses import dataclass, field

import pandas as pd


@dataclass
class QualityReport:
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    metrics: dict = field(default_factory=dict)

    @property
    def ok(self):
        return not self.errors

    def print(self):
        print("\nQA feedu:")
        for k, v in self.metrics.items():
            print(f"  {k}: {v}")
        for x in self.warnings:
            print(f"  VAROVÁNÍ: {x}")
        for x in self.errors:
            print(f"  CHYBA: {x}")
        print("  výsledek:", "OK" if self.ok else "ODMÍTNOUT FEED")


def validate(hourly: pd.DataFrame, trip_state: pd.DataFrame,
             prev_hourly: pd.DataFrame | None = None,
             prev_trip_state: pd.DataFrame | None = None) -> QualityReport:
    r = QualityReport()
    r.metrics = {
        "řádků hodinového stavu": len(hourly),
        "průjezdů úsekem": len(trip_state),
        "linek": hourly["line"].nunique() if len(hourly) else 0,
        "dnů": hourly["date"].nunique() if len(hourly) else 0,
        "unikátních úseků": hourly[["line","direction_id","stop_a_id","stop_b_id"]].drop_duplicates().shape[0] if len(hourly) else 0,
    }

    if hourly.empty:
        r.errors.append("Hodinový stav je prázdný.")
    if trip_state.empty:
        r.errors.append("Stav konkrétních spojů je prázdný.")
    if len(hourly) and hourly["line"].nunique() < 5:
        r.errors.append("Feed obsahuje podezřele málo tramvajových linek.")
    if len(hourly) and hourly["date"].nunique() < 2:
        r.errors.append("Feed obsahuje méně než dva aktivní kalendářní dny tramvají.")

    if len(hourly):
        med = pd.to_numeric(hourly["median_s"], errors="coerce")
        if med.isna().any():
            r.errors.append("Některé agregované jízdní doby nejsou číselné.")
        if (med <= 0).any():
            r.errors.append("Feed obsahuje nulovou nebo zápornou jízdní dobu.")
        absurd = int((med > 30 * 60).sum())
        if absurd:
            r.warnings.append(f"{absurd} úseků má plánovanou jízdní dobu přes 30 minut.")

    if len(trip_state):
        dur = pd.to_numeric(trip_state["dur_s"], errors="coerce")
        dep = pd.to_numeric(trip_state["dep_s"], errors="coerce")
        if dur.isna().any() or dep.isna().any():
            r.errors.append("Stav spojů obsahuje nečíselný čas.")
        if (dur <= 0).any():
            r.errors.append("Stav spojů obsahuje nulovou nebo zápornou jízdní dobu.")
        if (dep < 0).any() or (dep >= 36 * 3600).any():
            r.errors.append("Stav spojů obsahuje odjezd mimo očekávaný rozsah 00:00–35:59.")

    # Relativní kontroly jsou záměrně konzervativní: tvrdě zastavujeme až katastrofální propad.
    if prev_hourly is not None and len(prev_hourly) and len(hourly):
        old_lines, new_lines = prev_hourly["line"].nunique(), hourly["line"].nunique()
        if new_lines < old_lines * 0.60:
            r.errors.append(f"Počet linek klesl z {old_lines} na {new_lines} (>40 %).")
        old_segments = prev_hourly[["line","direction_id","stop_a_id","stop_b_id"]].drop_duplicates().shape[0]
        new_segments = hourly[["line","direction_id","stop_a_id","stop_b_id"]].drop_duplicates().shape[0]
        if new_segments < old_segments * 0.60:
            r.errors.append(f"Počet unikátních úseků klesl z {old_segments} na {new_segments} (>40 %).")
        elif new_segments < old_segments * 0.80:
            r.warnings.append(f"Počet unikátních úseků klesl z {old_segments} na {new_segments} (>20 %).")

    if prev_trip_state is not None and len(prev_trip_state) and len(trip_state):
        common = set(prev_trip_state["date"]) & set(trip_state["date"])
        if common:
            old_n = len(prev_trip_state[prev_trip_state["date"].isin(common)])
            new_n = len(trip_state[trip_state["date"].isin(common)])
            if old_n and new_n < old_n * 0.60:
                r.errors.append(f"Na společných dnech klesl počet průjezdů z {old_n} na {new_n} (>40 %).")
            elif old_n and new_n < old_n * 0.80:
                r.warnings.append(f"Na společných dnech klesl počet průjezdů z {old_n} na {new_n} (>20 %).")

    return r

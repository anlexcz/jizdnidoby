import unittest

import pandas as pd

import classify_variants as cv


class ClassifyVariantTests(unittest.TestCase):
    def setUp(self):
        self.depots = {
            "Hotel Golf": [{"depot_id": "motol", "depot_name": "Motol", "strength": "normal", "can_start_run": True, "can_end_run": True}],
            "Vozovna Motol": [{"depot_id": "motol", "depot_name": "Motol", "strength": "strong", "can_start_run": True, "can_end_run": True}],
        }

    def frame(self, rows):
        df = pd.DataFrame(rows)
        df["group_clean_total"] = df.groupby(["service_date", "line", "direction_id"])["clean_trip_count"].transform("sum")
        return df

    def test_stable_depot_terminal_is_regular_base(self):
        rows = []
        for day in ("2026-11-03", "2026-11-04", "2026-11-05"):
            rows.append({
                "service_date": day, "line": "93", "direction_id": "0", "route_variant_id": "main",
                "trip_count": 8, "clean_trip_count": 8, "operational_trip_count": 0, "operation_patterns": "",
                "first_stop_name": "Vozovna Motol", "last_stop_name": "Cíl", "is_night": "1",
            })
        out = cv.classify_rows(self.frame(rows), self.depots)
        self.assertTrue((out["classification"] == "regular_base").all())

    def test_hidden_depot_in_after_regular_exclusion(self):
        rows = [
            {"service_date": "2026-11-03", "line": "98", "direction_id": "1", "route_variant_id": "main",
             "trip_count": 9, "clean_trip_count": 9, "operational_trip_count": 0, "operation_patterns": "",
             "first_stop_name": "A", "last_stop_name": "B", "is_night": "1"},
            {"service_date": "2026-11-03", "line": "98", "direction_id": "1", "route_variant_id": "zatah",
             "trip_count": 1, "clean_trip_count": 1, "operational_trip_count": 0, "operation_patterns": "",
             "first_stop_name": "B", "last_stop_name": "Vozovna Motol", "is_night": "1"},
        ]
        out = cv.classify_rows(self.frame(rows), self.depots)
        row = out[out["route_variant_id"] == "zatah"].iloc[0]
        self.assertEqual(row["classification"], "depot_in")

    def test_explicit_transition_has_priority_without_clean_trips(self):
        rows = [{
            "service_date": "2026-11-03", "line": "9", "direction_id": "0", "route_variant_id": "x",
            "trip_count": 2, "clean_trip_count": 0, "operational_trip_count": 2, "operation_patterns": "10>1",
            "first_stop_name": "A", "last_stop_name": "B", "is_night": "0",
        }]
        out = cv.classify_rows(self.frame(rows), self.depots)
        self.assertEqual(out.iloc[0]["classification"], "transition_other_line")
        self.assertEqual(out.iloc[0]["confidence"], "explicit")

    def test_substantial_one_day_variant_is_regular_day_variant(self):
        rows = [
            {"service_date": "2026-11-03", "line": "22", "direction_id": "0", "route_variant_id": "base",
             "trip_count": 30, "clean_trip_count": 30, "operational_trip_count": 0, "operation_patterns": "",
             "first_stop_name": "A", "last_stop_name": "B", "is_night": "0"},
            {"service_date": "2026-11-03", "line": "22", "direction_id": "0", "route_variant_id": "diversion",
             "trip_count": 20, "clean_trip_count": 20, "operational_trip_count": 0, "operation_patterns": "",
             "first_stop_name": "C", "last_stop_name": "D", "is_night": "0"},
            {"service_date": "2026-11-04", "line": "22", "direction_id": "0", "route_variant_id": "base",
             "trip_count": 50, "clean_trip_count": 50, "operational_trip_count": 0, "operation_patterns": "",
             "first_stop_name": "A", "last_stop_name": "B", "is_night": "0"},
        ]
        out = cv.classify_rows(self.frame(rows), self.depots)
        row = out[(out["service_date"] == "2026-11-03") & (out["route_variant_id"] == "diversion")].iloc[0]
        self.assertEqual(row["classification"], "regular_day_variant")


if __name__ == "__main__":
    unittest.main()

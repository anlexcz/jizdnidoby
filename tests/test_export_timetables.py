import unittest

import pandas as pd

import export_timetables as et


class TimetableRouteTests(unittest.TestCase):
    def test_high_volume_regular_trip_wins_over_long_depot_variant(self):
        candidates = {
            ("2026-11-05", "1", "0"): [
                {"id": "depot", "count": 2, "class": "depot_out", "snapshot": "x"},
                {"id": "main", "count": 60, "class": "regular_base", "snapshot": "x"},
            ],
            ("2026-11-06", "1", "0"): [
                {"id": "main", "count": 55, "class": "regular_base", "snapshot": "y"},
            ],
        }
        # Filtering is normally done by route_candidates, before choosing.
        regular = {key: [row for row in rows
                         if row["class"] in et.REGULAR_CLASSES]
                   for key, rows in candidates.items()}
        primary = et.choose_main_variants(regular)
        self.assertEqual(primary[("1", "0")], "main")
        self.assertEqual(et.choose_route(regular[("2026-11-05", "1", "0")], "main")["id"], "main")

    def test_route_does_not_join_segments_from_different_trips(self):
        regular_stops = [["A", "Start"], ["B", "Middle"], ["C", "Terminus"]]
        # X -> A and C -> Y belong to depot moves but must never extend the route.
        state = pd.DataFrame([
            {"stop_a_id": "X", "stop_b_id": "A", "hour": "8", "median_s": "60"},
            {"stop_a_id": "A", "stop_b_id": "B", "hour": "8", "median_s": "120"},
            {"stop_a_id": "B", "stop_b_id": "C", "hour": "8", "median_s": "180"},
            {"stop_a_id": "C", "stop_b_id": "Y", "hour": "8", "median_s": "60"},
        ])
        result = et.snapshot_from_route(state, regular_stops, "main")
        self.assertEqual(result["stops"], regular_stops)
        self.assertEqual(result["hours"]["8"], [120, 180])
        self.assertEqual(result["variant_id"], "main")

    def test_missing_regular_route_is_not_replaced_by_operational_route(self):
        self.assertIsNone(et.choose_route([], "main"))

    def test_absent_segment_is_null_not_zero(self):
        state = pd.DataFrame([
            {"stop_a_id": "A", "stop_b_id": "B", "hour": "8", "median_s": "120"},
        ])
        result = et.snapshot_from_route(
            state, [["A", "Start"], ["B", "Middle"], ["C", "Terminus"]], "main"
        )
        self.assertEqual(result["hours"]["8"], [120, None])


if __name__ == "__main__":
    unittest.main()

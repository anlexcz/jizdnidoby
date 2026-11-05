import unittest

import pandas as pd

import export_variants_v3 as ev


class VariantsV3Tests(unittest.TestCase):
    def fixture(self):
        trips = pd.DataFrame([
            {"trip_id": "t1", "line": "3", "direction_id": "0", "route_variant_id": "bran", "clean_regular": "True"},
            {"trip_id": "t2", "line": "3", "direction_id": "0", "route_variant_id": "mod", "clean_regular": "True"},
            {"trip_id": "t3", "line": "3", "direction_id": "0", "route_variant_id": "mod", "clean_regular": "True"},
            {"trip_id": "depot", "line": "3", "direction_id": "0", "route_variant_id": "depot", "clean_regular": "False"},
        ])
        rows = []
        stop_sequences = {
            "t1": ["A", "B", "C"],
            "t2": ["A", "B", "C", "D"],
            "t3": ["A", "B", "C", "D"],
            "depot": ["X", "A", "B", "C", "D"],
        }
        for tid, stops in stop_sequences.items():
            for n, stop in enumerate(stops):
                rows.append({
                    "trip_id": tid, "stop_sequence": str(n + 1), "stop_id": stop,
                    "arrival_time": f"{8+n:02d}:00:00", "departure_time": f"{8+n:02d}:00:00",
                })
        vs = []
        for variant, seq in {
            "bran": ["A", "B", "C"], "mod": ["A", "B", "C", "D"],
            "depot": ["X", "A", "B", "C", "D"],
        }.items():
            for n, stop in enumerate(seq):
                vs.append({
                    "route_variant_id": variant, "stop_sequence": str(n+1),
                    "stop_id": stop, "stop_name": stop,
                })
        classes = pd.DataFrame([
            {"line": "3", "direction_id": "0", "route_variant_id": "bran",
             "classification": "regular_base", "clean_trip_count": 1},
            {"line": "3", "direction_id": "0", "route_variant_id": "mod",
             "classification": "regular_day_variant", "clean_trip_count": 2},
            {"line": "3", "direction_id": "0", "route_variant_id": "depot",
             "classification": "depot_out", "clean_trip_count": 0},
        ])
        return trips, pd.DataFrame(rows), pd.DataFrame(vs), classes

    def test_parallel_regular_variants(self):
        result = ev.build_day("2026-11-05", *self.fixture())
        variants = result["3"]["0"]["variants"]
        self.assertEqual(
            [(v["id"], v["trip_count"]) for v in variants],
            [("mod", 2), ("bran", 1)],
        )
        self.assertEqual(variants[0]["stops"][-1][0], "D")
        self.assertEqual(variants[1]["stops"][-1][0], "C")

    def test_depot_excluded(self):
        variants = ev.build_day("2026-11-05", *self.fixture())["3"]["0"]["variants"]
        self.assertNotIn("depot", [v["id"] for v in variants])

    def test_hours_and_segment_alignment(self):
        v = ev.build_day("2026-11-05", *self.fixture())["3"]["0"]["variants"][0]
        self.assertEqual(v["start_hours"], {"8": 2})
        self.assertEqual(v["hours"]["8"][0], 3600)
        self.assertEqual(len(v["hours"]["10"]), 3)

    def test_repeated_stop_preserved(self):
        trips, times, vs, classes = self.fixture()
        mod = vs[vs.route_variant_id == "mod"].copy()
        mod.loc[mod.stop_sequence == "3", "stop_id"] = "B"
        mod.loc[mod.stop_sequence == "3", "stop_name"] = "B"
        vs = pd.concat([vs[vs.route_variant_id != "mod"], mod])
        v = ev.build_day("2026-11-05", trips, times, vs, classes)["3"]["0"]["variants"][0]
        self.assertEqual([x[0] for x in v["stops"]], ["A", "B", "B", "D"])

    def test_gtfs_after_midnight(self):
        seconds = ev.trip_seconds(pd.Series(["23:50:00", "24:10:00", "25:01:30", "invalid"]))
        self.assertEqual(seconds.iloc[:3].tolist(), [85800, 87000, 90090])
        self.assertTrue(pd.isna(seconds.iloc[3]))


if __name__ == "__main__":
    unittest.main()

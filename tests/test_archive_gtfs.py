import unittest

import pandas as pd

import archive_gtfs


class ArchiveGtfsTests(unittest.TestCase):
    def test_route_variant_id_depends_on_full_sequence(self):
        a = archive_gtfs.route_variant_id("9", "0", ["a", "b", "c"])
        b = archive_gtfs.route_variant_id("9", "0", ["a", "x", "c"])
        c = archive_gtfs.route_variant_id("9", "1", ["a", "b", "c"])
        self.assertNotEqual(a, b)
        self.assertNotEqual(a, c)
        self.assertEqual(a, archive_gtfs.route_variant_id("9", "0", ["a", "b", "c"]))

    def test_split_operation_legs_keeps_operation_boundaries(self):
        rows = pd.DataFrame([
            {"trip_id": "t", "stop_sequence": "1", "stop_id": "A", "stop_name": "A", "trip_operation_type": "7"},
            {"trip_id": "t", "stop_sequence": "2", "stop_id": "B", "stop_name": "B", "trip_operation_type": "7"},
            {"trip_id": "t", "stop_sequence": "3", "stop_id": "C", "stop_name": "C", "trip_operation_type": "1"},
            {"trip_id": "t", "stop_sequence": "4", "stop_id": "D", "stop_name": "D", "trip_operation_type": "1"},
            {"trip_id": "t", "stop_sequence": "5", "stop_id": "E", "stop_name": "E", "trip_operation_type": "8"},
        ])
        legs = archive_gtfs.split_operation_legs(rows)
        self.assertEqual([x["operation_type"] for x in legs], ["7", "1", "8"])
        self.assertEqual((legs[0]["from_stop_id"], legs[0]["to_stop_id"]), ("A", "B"))
        self.assertEqual((legs[1]["from_stop_id"], legs[1]["to_stop_id"]), ("C", "D"))
        self.assertEqual((legs[2]["from_stop_id"], legs[2]["to_stop_id"]), ("E", "E"))

    def test_service_snapshot_hash_detects_content_change(self):
        trips = pd.DataFrame([{
            "trip_id": "t", "route_id": "L9", "line": "9", "direction_id": "0",
            "service_id": "svc", "block_id": "", "trip_headsign": "X", "shape_id": "",
            "is_night": "0", "exceptional": "", "route_variant_id": "v",
            "operation_pattern": "1", "clean_regular": True,
        }])
        stop_times = pd.DataFrame([
            {"trip_id": "t", "stop_sequence": "1", "stop_id": "A", "stop_name": "A",
             "stable_stop_key": "a", "arrival_time": "05:00:00", "departure_time": "05:00:00",
             "trip_operation_type": "1"},
            {"trip_id": "t", "stop_sequence": "2", "stop_id": "B", "stop_name": "B",
             "stable_stop_key": "b", "arrival_time": "05:01:00", "departure_time": "05:01:00",
             "trip_operation_type": "1"},
        ])
        base = archive_gtfs.service_snapshot_id(trips, stop_times)
        same = archive_gtfs.service_snapshot_id(trips.copy(), stop_times.copy())
        changed_times = stop_times.copy()
        changed_times.loc[1, "arrival_time"] = "05:02:00"
        changed = archive_gtfs.service_snapshot_id(trips, changed_times)
        self.assertEqual(base, same)
        self.assertNotEqual(base, changed)


if __name__ == "__main__":
    unittest.main()

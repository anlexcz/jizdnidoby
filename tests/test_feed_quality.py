import unittest

import pandas as pd

import feed_quality


def hourly(durations):
    rows = []
    for date in ["2026-11-02", "2026-11-03"]:
        for i, line in enumerate(["1", "2", "3", "4", "5"]):
            rows.append({
                "date": date,
                "line": line,
                "direction_id": "0",
                "stop_a_id": f"A{i}",
                "stop_b_id": f"B{i}",
                "median_s": durations[(i + (0 if date == "2026-11-02" else 5)) % len(durations)],
            })
    return pd.DataFrame(rows)


def trip_state(durations):
    rows = []
    for i, dur in enumerate(durations):
        rows.append({
            "date": "2026-11-02" if i % 2 == 0 else "2026-11-03",
            "line": str((i % 5) + 1),
            "direction_id": "0",
            "stop_a_id": f"A{i % 5}",
            "stop_b_id": f"B{i % 5}",
            "dep_s": 3600 + i * 300,
            "dur_s": dur,
        })
    return pd.DataFrame(rows)


class FeedQualityTests(unittest.TestCase):
    def test_zero_duration_is_warning_not_error(self):
        report = feed_quality.validate(hourly([120, 0, 180]), trip_state([120, 0, 180, 120, 180, 120]))
        self.assertTrue(report.ok)
        self.assertGreater(report.metrics["nulových agregovaných jízdních dob"], 0)
        self.assertEqual(report.metrics["nulových průjezdů úsekem"], 1)
        self.assertTrue(any("nulov" in warning for warning in report.warnings))

    def test_negative_duration_still_rejects_feed(self):
        report = feed_quality.validate(hourly([120, -1, 180]), trip_state([120, -1, 180, 120, 180, 120]))
        self.assertFalse(report.ok)
        self.assertTrue(any("záporn" in error for error in report.errors))

    def test_clean_feed_has_no_zero_duration_warning(self):
        report = feed_quality.validate(hourly([120, 150, 180]), trip_state([120, 150, 180, 120, 150, 180]))
        self.assertTrue(report.ok)
        self.assertEqual(report.metrics["nulových agregovaných jízdních dob"], 0)
        self.assertEqual(report.metrics["nulových průjezdů úsekem"], 0)


if __name__ == "__main__":
    unittest.main()

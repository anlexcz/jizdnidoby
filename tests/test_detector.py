import unittest
import pandas as pd

import daily_update as du


def trip_state(deps, durs, date="2026-11-02", line="17", direction="0", a="A", b="B"):
    return pd.DataFrame({
        "date": [date] * len(deps),
        "line": [line] * len(deps),
        "direction_id": [direction] * len(deps),
        "stop_a_id": [a] * len(deps),
        "stop_b_id": [b] * len(deps),
        "dep_s": deps,
        "dur_s": durs,
    })


def hourly(date="2026-11-02", line="17", direction="0", a="A", b="B"):
    return pd.DataFrame([{
        "date": date, "line": line, "direction_id": direction,
        "stop_a_id": a, "stop_a_name": "A name",
        "stop_b_id": b, "stop_b_name": "B name",
        "hour": 12, "median_s": 180, "n_trips": 5,
    }])


class DetectorTests(unittest.TestCase):
    def detect(self, old, new):
        return du.compare_trip_times(old, new, hourly(), "2026-11-01")

    def test_all_trips_plus_60_is_change(self):
        deps = [12*3600 + x*600 for x in range(5)]
        out = self.detect(trip_state(deps, [180]*5), trip_state(deps, [240]*5))
        self.assertEqual(len(out), 1)
        self.assertEqual(out.iloc[0].diff_s, 60)

    def test_single_changed_trip_is_not_change(self):
        deps = [12*3600 + x*600 for x in range(5)]
        out = self.detect(trip_state(deps, [180]*5), trip_state(deps, [240,180,180,180,180]))
        self.assertTrue(out.empty)

    def test_crossing_hour_boundary_does_not_create_change(self):
        old_deps = [12*3600 + 39*60, 12*3600 + 49*60, 12*3600 + 59*60]
        new_deps = [x + 2*60 for x in old_deps]
        out = self.detect(trip_state(old_deps, [180]*3), trip_state(new_deps, [180]*3))
        self.assertTrue(out.empty)

    def test_departure_shift_with_same_duration_is_not_change(self):
        deps = [12*3600 + x*600 for x in range(5)]
        out = self.detect(trip_state(deps, [180]*5), trip_state([x+300 for x in deps], [180]*5))
        self.assertTrue(out.empty)

    def test_less_than_three_matches_is_not_change(self):
        deps = [12*3600, 12*3600+600]
        out = self.detect(trip_state(deps, [180,180]), trip_state(deps, [240,240]))
        self.assertTrue(out.empty)

    def test_three_of_four_changed_is_below_80_percent(self):
        deps = [12*3600 + x*600 for x in range(4)]
        out = self.detect(trip_state(deps, [180]*4), trip_state(deps, [240,240,240,180]))
        self.assertTrue(out.empty)

    def test_four_of_five_changed_passes_80_percent(self):
        deps = [12*3600 + x*600 for x in range(5)]
        out = self.detect(trip_state(deps, [180]*5), trip_state(deps, [240,240,240,240,180]))
        self.assertEqual(len(out), 1)


if __name__ == "__main__":
    unittest.main()

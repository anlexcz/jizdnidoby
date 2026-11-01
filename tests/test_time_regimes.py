import unittest
import pandas as pd
import time_regimes as tr

BASE = {"detected_on":"2026-11-02","date":"2026-11-09","line":"17","direction_id":"0",
        "stop_a_id":"A","stop_a_name":"A","stop_b_id":"B","stop_b_name":"B","kind":"time_changed"}

def rows(hours, diffs=None):
    diffs = diffs or [60] * len(hours)
    return pd.DataFrame([{**BASE,"hour":h,"old_s":120,"new_s":120+d,"diff_s":d} for h,d in zip(hours,diffs)])

class RegimeTests(unittest.TestCase):
    def test_consecutive_hours_merge(self):
        out=tr.build(rows([6,7,8])); self.assertEqual(len(out),1); self.assertEqual(out.iloc[0].start_hour,6); self.assertEqual(out.iloc[0].end_hour,9)
    def test_gap_splits_regime(self):
        out=tr.build(rows([6,7,9])); self.assertEqual([(r.start_hour,r.end_hour) for r in out.itertuples()],[(6,8),(9,10)])
    def test_different_change_splits_regime(self):
        out=tr.build(rows([6,7,8],[60,60,120])); self.assertEqual([(r.start_hour,r.end_hour) for r in out.itertuples()],[(6,8),(8,9)])
    def test_other_change_kinds_are_ignored(self):
        x=rows([6]); x.loc[0,"kind"]="segment_added"; self.assertTrue(tr.build(x).empty)

if __name__=="__main__": unittest.main()

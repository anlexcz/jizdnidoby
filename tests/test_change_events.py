import unittest
import pandas as pd
import change_events

class EventTests(unittest.TestCase):
    def test_adjacent_segments_merge(self):
        base={"detected_on":"2026-11-02","line":"17","direction_id":"0","start_hour":6,"end_hour":9,"old_s":120,"new_s":180,"diff_s":60,"validity":"weekdays","valid_from":"2026-11-09","valid_to":"2026-11-13","dates":"2026-11-09,2026-11-10,2026-11-11,2026-11-12,2026-11-13","matched_trips":10,"supporting_trips":10}
        df=pd.DataFrame([{**base,"stop_a_id":"A","stop_a_name":"A","stop_b_id":"B","stop_b_name":"B"},{**base,"stop_a_id":"B","stop_a_name":"B","stop_b_id":"C","stop_b_name":"C"}])
        out=change_events.build(df); self.assertEqual(len(out),1); self.assertEqual(out[0]["stop_a_id"],"A"); self.assertEqual(out[0]["stop_b_id"],"C"); self.assertEqual(len(out[0]["segments"]),2); self.assertEqual(out[0]["confidence"],1.0)
    def test_non_adjacent_segments_stay_separate(self):
        base={"detected_on":"x","line":"1","direction_id":"0","start_hour":6,"end_hour":7,"old_s":60,"new_s":120,"diff_s":60,"validity":"dates","valid_from":"x","valid_to":"x","dates":"x","matched_trips":5,"supporting_trips":4}
        df=pd.DataFrame([{**base,"stop_a_id":"A","stop_a_name":"A","stop_b_id":"B","stop_b_name":"B"},{**base,"stop_a_id":"C","stop_a_name":"C","stop_b_id":"D","stop_b_name":"D"}]); self.assertEqual(len(change_events.build(df)),2)
if __name__=="__main__": unittest.main()

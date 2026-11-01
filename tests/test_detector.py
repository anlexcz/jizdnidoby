import unittest
import pandas as pd
import daily_update as du


def trip_state(deps,durs,date="2026-11-02",line="17",direction="0",a="A",b="B",a_key=None,b_key=None):
    return pd.DataFrame({"date":[date]*len(deps),"line":[line]*len(deps),"direction_id":[direction]*len(deps),
        "stop_a_id":[a]*len(deps),"stop_b_id":[b]*len(deps),"stop_a_key":[a_key or a]*len(deps),"stop_b_key":[b_key or b]*len(deps),"dep_s":deps,"dur_s":durs})

def hourly(date="2026-11-02",line="17",direction="0",a="A",b="B"):
    return pd.DataFrame([{"date":date,"line":line,"direction_id":direction,"stop_a_id":a,"stop_a_name":"A name","stop_b_id":b,"stop_b_name":"B name","hour":12,"median_s":180,"n_trips":5}])

class DetectorTests(unittest.TestCase):
    def detect(self,old,new,h=None): return du.compare_trip_times(old,new,h if h is not None else hourly(),"2026-11-01")
    def test_all_trips_plus_60_is_change(self):
        d=[43200+x*600 for x in range(5)]; o=self.detect(trip_state(d,[180]*5),trip_state(d,[240]*5)); self.assertEqual(len(o),1); self.assertEqual(o.iloc[0].diff_s,60)
    def test_single_changed_trip_is_not_change(self):
        d=[43200+x*600 for x in range(5)]; self.assertTrue(self.detect(trip_state(d,[180]*5),trip_state(d,[240,180,180,180,180])).empty)
    def test_crossing_hour_boundary_does_not_create_change(self):
        d=[45540,46140,46740]; self.assertTrue(self.detect(trip_state(d,[180]*3),trip_state([x+120 for x in d],[180]*3)).empty)
    def test_departure_shift_with_same_duration_is_not_change(self):
        d=[43200+x*600 for x in range(5)]; self.assertTrue(self.detect(trip_state(d,[180]*5),trip_state([x+300 for x in d],[180]*5)).empty)
    def test_less_than_three_matches_is_not_change(self):
        d=[43200,43800]; self.assertTrue(self.detect(trip_state(d,[180]*2),trip_state(d,[240]*2)).empty)
    def test_three_of_four_changed_is_below_80_percent(self):
        d=[43200+x*600 for x in range(4)]; self.assertTrue(self.detect(trip_state(d,[180]*4),trip_state(d,[240,240,240,180])).empty)
    def test_four_of_five_changed_passes_80_percent(self):
        d=[43200+x*600 for x in range(5)]; self.assertEqual(len(self.detect(trip_state(d,[180]*5),trip_state(d,[240,240,240,240,180]))),1)
    def test_changed_gtfs_stop_id_with_same_pid_identity_still_matches(self):
        d=[43200+x*600 for x in range(5)]
        old=trip_state(d,[180]*5,a="U237Z1-old",b="U238Z1-old",a_key="asw:237/1",b_key="asw:238/1")
        new=trip_state(d,[240]*5,a="U237Z1-new",b="U238Z1-new",a_key="asw:237/1",b_key="asw:238/1")
        out=self.detect(old,new,hourly(a="U237Z1-new",b="U238Z1-new")); self.assertEqual(len(out),1); self.assertEqual(out.iloc[0].stop_a_id,"U237Z1-new")
    def test_different_pid_stop_identity_does_not_match(self):
        d=[43200+x*600 for x in range(5)]; old=trip_state(d,[180]*5,a_key="asw:237/1",b_key="asw:238/1"); new=trip_state(d,[240]*5,a_key="asw:999/1",b_key="asw:238/1"); self.assertTrue(self.detect(old,new).empty)

if __name__=="__main__": unittest.main()

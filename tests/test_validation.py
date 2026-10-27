"""Unit tests for STMS-3 validation. Run:  python -m unittest discover tests"""
import os, sys, unittest, datetime
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
os.environ["STMS_DB"] = os.path.join(os.path.dirname(__file__), "test.db")
from app import validate_preferences, build_itinerary

TODAY = datetime.date(2026, 11, 1)

def form(**kw):
    base = dict(destination="Goa", start_date="2026-11-10", end_date="2026-11-14",
                budget="20000", travelers="2", interests=["beach"])
    base.update(kw)
    return base

class TestPreferences(unittest.TestCase):
    def errs(self, **kw):
        return validate_preferences(form(**kw), today=TODAY)[0]

    def test_valid(self):            self.assertEqual(self.errs(), [])
    def test_empty_destination(self): self.assertTrue(self.errs(destination=""))
    def test_no_interest(self):       self.assertTrue(self.errs(interests=[]))
    def test_past_start(self):        self.assertTrue(self.errs(start_date="2026-10-31"))
    def test_start_today_ok(self):    self.assertEqual(self.errs(start_date="2026-11-01", end_date="2026-11-02"), [])
    def test_end_before_start(self):  self.assertTrue(self.errs(end_date="2026-11-09"))
    def test_one_day_trip(self):      self.assertEqual(self.errs(end_date="2026-11-10"), [])
    def test_budget_boundaries(self):
        for b, bad in [("-1", True), ("0", True), ("1", False), ("abc", True)]:
            self.assertEqual(bool(self.errs(budget=b)), bad, b)
    def test_travelers_boundaries(self):
        for t, bad in [("0", True), ("1", False), ("20", False), ("21", True), ("2.5", True), ("x", True)]:
            self.assertEqual(bool(self.errs(travelers=t)), bad, t)
    def test_trip_too_long(self):     self.assertTrue(self.errs(end_date="2026-12-31"))

class TestItinerary(unittest.TestCase):
    def test_days_match_duration(self):
        self.assertEqual(len(build_itinerary("Goa", 5, ["beach"], 2)), 5)
    def test_no_activities(self):
        self.assertEqual(build_itinerary("Goa", 3, ["shopping"], 1), [])
    def test_cost_scales_with_travelers(self):
        one = build_itinerary("Goa", 1, ["beach"], 1)[0][2]
        self.assertEqual(build_itinerary("Goa", 1, ["beach"], 3)[0][2], one * 3)

if __name__ == "__main__":
    unittest.main()
